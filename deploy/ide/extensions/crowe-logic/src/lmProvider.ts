/**
 * Crowe Logic language model chat provider.
 *
 * Bridges VS Code's `vscode.LanguageModelChatProvider` API to the
 * Foundry headless runner. VS Code 1.117 made an LM provider a hard
 * prerequisite for chat participants: every participant turn now goes
 * through getDefaultLanguageModel(), and if no model is registered the
 * chat infrastructure throws "Language model unavailable" before the
 * participant's handler runs. Registering this provider satisfies that
 * precondition AND lets any caller of `vscode.lm.selectChatModels()`
 * use Crowe Logic models without going through Copilot.
 *
 * The provider exposes Crowe Logic models (CroweLM Supreme by default)
 * and proxies every request to `cli.headless` over stdio, the same
 * code path the @crowe chat participant uses. This means the chat
 * webview, the participant, and any external LM consumer all share
 * one inference pipeline, one credit ledger, and one event protocol.
 */

import * as vscode from 'vscode';
import { runFoundryTurn, FoundryMessage, FoundryEvent } from './agent';
import { resolveFoundryPath, resolvePythonPath, pythonNotFoundMessage } from './resolvePaths';

// VS Code 1.117 hardcodes its default-LM resolver to vendor === "copilot",
// which means our provider can never become the workspace default. We
// previously tried claiming the copilot vendor; that satisfied the resolver
// but triggered VS Code's "You need to set up GitHub Copilot and be signed
// in to use Chat" UI, which is wired to the real Copilot extension and
// cannot be turned off from product.json alone. So we register under our
// own vendor and rely on the user (or our auto-select on activation)
// picking CroweLM in the chat model picker, which sets userSelectedModelId
// and bypasses the default lookup entirely. The dedicated Crowe Logic chat
// webview at views/chatView.ts skips VS Code's chat infrastructure
// altogether and works regardless of this gating.
const VENDOR = 'crowe-logic';

interface CroweLogicModelInfo extends vscode.LanguageModelChatInformation {
    foundryModel: string;
    // Proposed-API metadata (chatProvider proposal). Not on the public type
    // surface, but the underlying API accepts it: VS Code uses
    // isDefaultForLocation.panel to pick the default model in the chat panel.
    // We declare the cast inline below rather than augmenting global types.
}

// Cast helper: the extra fields below are part of the chatProvider proposed
// API but not exposed on @types/vscode's public interface. Using `as any`
// keeps the runtime payload correct without requiring a parallel proposed.d.ts.
const MODELS: CroweLogicModelInfo[] = ([
    {
        id: 'crowelm-auto',
        name: 'CroweLM Auto',
        family: 'crowelm',
        version: '1.0',
        tooltip: 'CroweLM Auto. Routes each turn to the best-fit tier.',
        detail: 'Smart router',
        maxInputTokens: 200_000,
        maxOutputTokens: 16_000,
        foundryModel: 'auto',
        capabilities: { toolCalling: true },
        isDefaultForLocation: { panel: true },
        isUserSelectable: true,
    },
    {
        id: 'crowelm-supreme',
        name: 'CroweLM Supreme',
        family: 'crowelm',
        version: '1.0',
        tooltip: 'CroweLM Supreme. Flagship reasoning tier.',
        detail: 'Top tier',
        maxInputTokens: 200_000,
        maxOutputTokens: 16_000,
        foundryModel: 'crowelm-supreme',
        capabilities: { toolCalling: true },
        isUserSelectable: true,
    },
    {
        id: 'crowelm-apex',
        name: 'CroweLM Apex',
        family: 'crowelm',
        version: '1.0',
        tooltip: 'CroweLM Apex. Peak-performance reasoning, fast responses.',
        detail: 'Fast tier',
        maxInputTokens: 128_000,
        maxOutputTokens: 8_000,
        foundryModel: 'crowelm-apex',
        capabilities: { toolCalling: true },
        isUserSelectable: true,
    },
    {
        id: 'crowelm-titan',
        name: 'CroweLM Titan',
        family: 'crowelm',
        version: '1.0',
        tooltip: 'CroweLM Titan. High-capacity flagship for complex synthesis.',
        detail: 'High capacity',
        maxInputTokens: 200_000,
        maxOutputTokens: 16_000,
        foundryModel: 'crowelm-titan',
        capabilities: { toolCalling: true },
        isUserSelectable: true,
    },
    {
        id: 'crowelm-oracle',
        name: 'CroweLM Oracle',
        family: 'crowelm',
        version: '1.0',
        tooltip: 'CroweLM Oracle. Deep-foresight reasoning with grounded multimodal.',
        detail: 'Deep reasoning',
        maxInputTokens: 200_000,
        maxOutputTokens: 16_000,
        foundryModel: 'crowelm-oracle',
        capabilities: { toolCalling: true },
        isUserSelectable: true,
    },
    {
        id: 'crowelm-kernel',
        name: 'CroweLM Kernel',
        family: 'crowelm',
        version: '1.0',
        tooltip: 'CroweLM Kernel. Cultivation-tuned fast tier — Lions Mane SOPs, mycology research, grow-room operational data.',
        detail: 'Cultivation fast',
        maxInputTokens: 64_000,
        maxOutputTokens: 4_000,
        foundryModel: 'crowelm-kernel',
        capabilities: { toolCalling: true },
        isUserSelectable: true,
    },
    {
        id: 'crowelm-grower',
        name: 'CroweLM Grower',
        family: 'crowelm',
        version: '1.0',
        tooltip: 'CroweLM Grower. Commercial mycology operations specialist — substrate prep, sterilization, contamination triage, yield analysis.',
        detail: 'Cultivation specialist',
        maxInputTokens: 64_000,
        maxOutputTokens: 4_000,
        foundryModel: 'crowelm-grower',
        capabilities: { toolCalling: true },
        isUserSelectable: true,
    },
] as any) as CroweLogicModelInfo[];

/**
 * Extract the plain-text payload from a VS Code chat-request message.
 * Tool-call and tool-result parts are dropped because the Foundry agent
 * runs its own tool registry; mixing VS Code's tool envelope into the
 * prompt would just confuse the model. Data parts (images, etc.) are
 * also dropped for now. The content array is typed as
 * `ReadonlyArray<LanguageModelInputPart | unknown>` so we accept the
 * wider type and filter with instanceof.
 */
function flattenContent(parts: ReadonlyArray<unknown>): string {
    const out: string[] = [];
    for (const part of parts) {
        if (part instanceof vscode.LanguageModelTextPart) {
            out.push(part.value);
        }
    }
    return out.join('').trim();
}

function roleLabel(role: vscode.LanguageModelChatMessageRole): 'user' | 'assistant' {
    return role === vscode.LanguageModelChatMessageRole.Assistant ? 'assistant' : 'user';
}

/**
 * Convert VS Code's LanguageModelChatRequestMessage[] to FoundryMessage[].
 * VS Code's role enum only exposes User and Assistant in stable 1.117;
 * any system-style preamble would arrive as a User message containing
 * pre-formatted instructions, so we forward it as-is.
 */
function convertMessages(messages: readonly vscode.LanguageModelChatRequestMessage[]): FoundryMessage[] {
    const turns: FoundryMessage[] = [];
    for (const msg of messages) {
        const text = flattenContent(msg.content);
        if (!text) continue;
        turns.push({ role: roleLabel(msg.role), content: text });
    }
    return turns;
}

export class CroweLogicLanguageModelChatProvider
    implements vscode.LanguageModelChatProvider<CroweLogicModelInfo> {

    async provideLanguageModelChatInformation(
        _options: vscode.PrepareLanguageModelChatModelOptions,
        _token: vscode.CancellationToken,
    ): Promise<CroweLogicModelInfo[]> {
        return MODELS;
    }

    async provideLanguageModelChatResponse(
        model: CroweLogicModelInfo,
        messages: readonly vscode.LanguageModelChatRequestMessage[],
        _options: vscode.ProvideLanguageModelChatResponseOptions,
        progress: vscode.Progress<vscode.LanguageModelResponsePart>,
        token: vscode.CancellationToken,
    ): Promise<void> {
        const foundryPath = resolveFoundryPath();
        const pythonPath = resolvePythonPath(foundryPath);
        if (!pythonPath) {
            throw new Error(pythonNotFoundMessage(foundryPath));
        }

        const foundryMessages = convertMessages(messages);
        if (foundryMessages.length === 0) {
            return;
        }

        const events = runFoundryTurn(
            { messages: foundryMessages, model: model.foundryModel, session: undefined },
            { pythonPath, foundryPath, cancellation: token },
        );

        for await (const evt of events) {
            if (token.isCancellationRequested) break;
            if (evt.type === 'token' || evt.type === 'reasoning') {
                if (evt.delta) {
                    progress.report(new vscode.LanguageModelTextPart(evt.delta));
                }
            } else if (evt.type === 'error') {
                throw new Error(evt.message || `Foundry error (${evt.kind})`);
            }
            // 'tool', 'spinner', 'segment_end', 'ready', 'done' are ignored
            // for the LM API surface. The chat participant still sees them
            // through its own runFoundryTurn call when it decides to use
            // tools, but the LM contract only cares about response text.
        }
    }

    /**
     * Approximate token count. The Foundry agent has accurate per-model
     * tokenizers but they live Python-side; for the synchronous LM API
     * we use the standard ~4-chars-per-token heuristic. Good enough for
     * VS Code's UI estimates; not used for billing.
     */
    async provideTokenCount(
        _model: CroweLogicModelInfo,
        text: string | vscode.LanguageModelChatRequestMessage,
        _token: vscode.CancellationToken,
    ): Promise<number> {
        const raw = typeof text === 'string' ? text : flattenContent(text.content);
        return Math.ceil(raw.length / 4);
    }
}

export function registerCroweLogicLanguageModel(context: vscode.ExtensionContext): void {
    const provider = new CroweLogicLanguageModelChatProvider();
    context.subscriptions.push(
        vscode.lm.registerLanguageModelChatProvider(VENDOR, provider),
    );
}
