/**
 * Crowe Logic VS Code extension entry point.
 *
 * Registers a chat participant (`@crowe`) backed by the Foundry
 * headless runner, plus the Plan and Tool Activity views in the
 * Crowe Logic activity-bar container. The chat participant takes
 * the place of any default Copilot agent in product.json overrides
 * (see deploy/ide/product-overrides.json).
 */

import * as vscode from 'vscode';
import * as path from 'path';
import { createHash } from 'crypto';
import { runFoundryTurn, FoundryMessage, FoundryEvent } from './agent';
import { PlanViewProvider } from './views/planView';
import { ToolsViewProvider, ToolEntry } from './views/toolsView';
import { signIn, signOut } from './commands/signIn';
import { openInRemoteIde } from './commands/openInRemoteIde';
import { askWithContext } from './commands/askWithContext';
import { CroweCodeActionProvider, CROWE_CODE_ACTION_KINDS } from './codeActions';
import { registerStatusBar } from './statusBar';
import { resolveFoundryPath, resolvePythonPath, pythonNotFoundMessage, clearPathCache } from './resolvePaths';

export function activate(context: vscode.ExtensionContext) {
    const planView = new PlanViewProvider();
    const toolsView = new ToolsViewProvider();

    context.subscriptions.push(
        vscode.window.registerTreeDataProvider('crowe-logic.plan', planView),
        vscode.window.registerTreeDataProvider('crowe-logic.tools', toolsView),
    );

    const participant = vscode.chat.createChatParticipant(
        'crowe-logic.foundry',
        async (request, chatContext, stream, token) => {
            return handleChat(request, chatContext, stream, token, toolsView);
        }
    );

    // Avatar in the chat surface — this is the part that displaces
    // the default Copilot icon. Both light and dark variants point at
    // the same asset for now; swap to dedicated variants if a darker
    // mark is added later.
    participant.iconPath = {
        light: vscode.Uri.file(path.join(context.extensionPath, 'media', 'avatar-light.png')),
        dark: vscode.Uri.file(path.join(context.extensionPath, 'media', 'avatar-dark.png')),
    };

    participant.followupProvider = {
        provideFollowups(_result, _context, _token) {
            return [
                { prompt: 'Plan the next step', label: '📋 Plan next step', command: 'plan' },
                { prompt: 'Run the current plan', label: '▶ Run plan', command: 'run' },
            ];
        }
    };

    context.subscriptions.push(
        participant,
        vscode.commands.registerCommand('crowe-logic.openChat', async () => {
            await vscode.commands.executeCommand('workbench.action.chat.open', { query: '@crowe ' });
        }),
        vscode.commands.registerCommand('crowe-logic.clearTools', () => {
            toolsView.clear();
        }),
        vscode.commands.registerCommand('crowe-logic.togglePlan', () => {
            vscode.commands.executeCommand('crowe-logic.plan.focus');
        }),
        vscode.commands.registerCommand('crowe-logic.signIn', () => signIn(context)),
        vscode.commands.registerCommand('crowe-logic.signOut', () => signOut(context)),
        vscode.commands.registerCommand('crowe-logic.openInRemoteIde', () => openInRemoteIde(context)),
        vscode.commands.registerCommand('crowe-logic.askWithContext', (args) => askWithContext(args)),
        vscode.languages.registerCodeActionsProvider(
            { scheme: 'file' },
            new CroweCodeActionProvider(),
            { providedCodeActionKinds: CROWE_CODE_ACTION_KINDS },
        ),
    );

    void registerStatusBar(context);

    // Invalidate the resolver cache when the user edits pythonPath or foundryPath.
    context.subscriptions.push(
        vscode.workspace.onDidChangeConfiguration((e) => {
            if (e.affectsConfiguration('croweLogic.pythonPath') || e.affectsConfiguration('croweLogic.foundryPath')) {
                clearPathCache();
            }
        }),
    );
}

export function deactivate() { /* no-op */ }

/**
 * Single chat turn: convert VS Code's chat history into the
 * Foundry headless protocol shape, run the turn, and pipe events
 * into the chat stream and tool view.
 */
async function handleChat(
    request: vscode.ChatRequest,
    chatContext: vscode.ChatContext,
    stream: vscode.ChatResponseStream,
    token: vscode.CancellationToken,
    toolsView: ToolsViewProvider,
): Promise<vscode.ChatResult | void> {
    const config = vscode.workspace.getConfiguration('croweLogic');
    const foundryPath = resolveFoundryPath();
    const pythonPath = resolvePythonPath(foundryPath);
    if (!pythonPath) {
        stream.markdown('> **' + pythonNotFoundMessage(foundryPath).replace(/\n/g, '\n> ') + '**');
        return { metadata: { errorKind: 'python-not-found' } };
    }
    const model = config.get<string>('model', 'auto');

    // Replay prior turns from VS Code's chat history. We only forward
    // user prompts and assistant responses — VS Code's chat history
    // also tracks tool messages and references, but the Foundry agent
    // recreates those itself when it runs tools, so we drop them.
    const messages: FoundryMessage[] = [];
    for (const turn of chatContext.history) {
        if (turn instanceof vscode.ChatRequestTurn) {
            messages.push({ role: 'user', content: turn.prompt });
        } else if (turn instanceof vscode.ChatResponseTurn) {
            const text = turn.response
                .map(part => {
                    if (part instanceof vscode.ChatResponseMarkdownPart) {
                        return part.value.value;
                    }
                    return '';
                })
                .join('');
            if (text) messages.push({ role: 'assistant', content: text });
        }
    }
    // Slash commands (e.g. /plan) are surfaced as request.command;
    // include them in the prompt so the agent sees the user intent.
    const prompt = request.command
        ? `/${request.command} ${request.prompt}`.trim()
        : request.prompt;
    messages.push({ role: 'user', content: prompt });
    const sessionId = resolveSessionId(request, chatContext);

    // Markdown batcher: VS Code's chat webview re-renders on every
    // stream.markdown() call, and a fast model emits 50+ tokens/sec.
    // We accumulate token deltas and flush them at most once per
    // 16ms (~60Hz), and synchronously on every non-token event so
    // tool cards and separators stay in their right place in the
    // output stream.
    let pendingMd = '';
    let flushTimer: NodeJS.Timeout | null = null;
    const flushMd = () => {
        if (flushTimer) { clearTimeout(flushTimer); flushTimer = null; }
        if (pendingMd) { stream.markdown(pendingMd); pendingMd = ''; }
    };
    const queueMd = (delta: string) => {
        pendingMd += delta;
        if (!flushTimer) flushTimer = setTimeout(flushMd, 16);
    };

    try {
        const events = runFoundryTurn(
            { messages, model, session: sessionId },
            { pythonPath, foundryPath, cancellation: token }
        );

        for await (const evt of events) {
            if (token.isCancellationRequested) break;
            dispatch(evt, stream, toolsView, queueMd, flushMd);
            if (evt.type === 'error') {
                return { metadata: { sessionId, errorKind: evt.kind } };
            }
            if (evt.type === 'done') {
                return {
                    metadata: {
                        sessionId,
                        tokens: evt.tokens,
                        reasoningTokens: evt.reasoning_tokens,
                        elapsedMs: evt.elapsed_ms,
                    },
                };
            }
        }
    } catch (e: any) {
        flushMd();
        stream.markdown(`\n\n**Crowe Logic error:** ${e?.message || String(e)}\n`);
        return { metadata: { sessionId, errorKind: 'host' } };
    } finally {
        flushMd();
    }

    return { metadata: { sessionId } };
}

function resolveSessionId(
    request: vscode.ChatRequest,
    chatContext: vscode.ChatContext,
): string {
    for (const turn of [...chatContext.history].reverse()) {
        if (!(turn instanceof vscode.ChatResponseTurn)) continue;
        const sessionId = turn.result.metadata?.sessionId;
        if (typeof sessionId === 'string' && sessionId.trim()) {
            return sessionId;
        }
    }

    const workspace = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? 'workspace';
    const seed = [
        vscode.env.sessionId,
        workspace,
        request.command ?? '',
        request.prompt,
        Date.now().toString(),
    ].join('\n');
    const digest = createHash('sha1').update(seed).digest('hex').slice(0, 12);
    return `vscode-${digest}`;
}

function formatMs(ms: number): string {
    return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

function dispatch(
    evt: FoundryEvent,
    stream: vscode.ChatResponseStream,
    toolsView: ToolsViewProvider,
    queueMd: (delta: string) => void,
    flushMd: () => void,
): void {
    switch (evt.type) {
        case 'token':
            queueMd(evt.delta);
            return;
        case 'reasoning':
            // VS Code chat doesn't yet have a first-class reasoning
            // surface, so we route reasoning through the progress
            // channel to keep it visually distinct from the answer.
            stream.progress(evt.delta);
            return;
        case 'spinner':
            if (evt.label) stream.progress(evt.label);
            return;
        case 'tool': {
            const entry: ToolEntry = {
                name: evt.name,
                args: evt.args,
                status: evt.status,
                durationMs: evt.duration_ms,
                result: evt.result,
                timestamp: Date.now(),
            };
            toolsView.push(entry);
            flushMd();
            const icon = evt.status === 'ok' ? '✓' : '✗';
            stream.markdown(`\n\n_${icon} ${evt.name} (${formatMs(evt.duration_ms)})_\n\n`);
            return;
        }
        case 'segment_end':
            // Boundary between rounds — render a thin separator so
            // multi-tool conversations stay readable.
            flushMd();
            stream.markdown('\n');
            return;
        case 'done':
            flushMd();
            stream.markdown(
                `\n\n_${evt.tokens} tokens · ${formatMs(evt.elapsed_ms)}` +
                (evt.reasoning_tokens ? ` · ${evt.reasoning_tokens} reasoning_` : '_')
            );
            return;
        case 'error':
            flushMd();
            stream.markdown(`\n\n**Crowe Logic error (${evt.kind}):** ${evt.message}\n`);
            return;
        case 'ready':
            return;
    }
}
