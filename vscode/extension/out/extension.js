"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.activate = activate;
exports.deactivate = deactivate;
const vscode = require("vscode");
const THEME_ID = 'Crowe Logic Dark';
const ICON_THEME_ID = 'crowe-logic-icons';
const FIRST_RUN_KEY = 'croweLogic.firstRunComplete';
const PAT_SECRET_KEY = 'croweLogic.pat';
const SYSTEM_PROMPT = [
    'You are Crowe Logic, the universal Crowe Logic agent powered by the CroweLM model stack.',
    'You speak with calm, precise, professional tone. Prefer concise answers and concrete code.',
    'Never refer to yourself as Copilot, GitHub Copilot, or an OpenAI/Anthropic model — you are Crowe Logic.',
    'Surface tool calls, model fallbacks, and reasoning steps in the Crowe Logic style (gold-on-graphite).',
].join(' ');
async function activate(ctx) {
    ctx.subscriptions.push(vscode.commands.registerCommand('crowe-logic.applyTheme', applyTheme), vscode.commands.registerCommand('crowe-logic.applyProductIcons', applyProductIcons), vscode.commands.registerCommand('crowe-logic.applyAll', () => applyAll(ctx)), vscode.commands.registerCommand('crowe-logic.showWelcome', () => vscode.commands.executeCommand('workbench.action.openWalkthrough', 'crowe-logic.crowe-logic-vscode#crowe-logic.getting-started', false)), vscode.commands.registerCommand('crowe-logic.signIn', () => signIn(ctx)), vscode.commands.registerCommand('crowe-logic.signOut', () => signOut(ctx)));
    applyTitleBar();
    vscode.workspace.onDidChangeConfiguration((e) => {
        if (e.affectsConfiguration('croweLogic.titleBarText'))
            applyTitleBar();
    });
    registerChatParticipant(ctx);
    const cfg = vscode.workspace.getConfiguration('croweLogic');
    const firstRun = !ctx.globalState.get(FIRST_RUN_KEY, false);
    if (firstRun && cfg.get('autoApplyOnFirstRun', true)) {
        await applyAll(ctx);
        await ctx.globalState.update(FIRST_RUN_KEY, true);
        vscode.commands.executeCommand('crowe-logic.showWelcome');
    }
}
function deactivate() { }
// ── Branding commands (existing) ─────────────────────────────────
async function applyTheme() {
    await vscode.workspace.getConfiguration().update('workbench.colorTheme', THEME_ID, vscode.ConfigurationTarget.Global);
    vscode.window.showInformationMessage(`Crowe Logic theme applied (${THEME_ID}).`);
}
async function applyProductIcons() {
    await vscode.workspace
        .getConfiguration()
        .update('workbench.productIconTheme', ICON_THEME_ID, vscode.ConfigurationTarget.Global);
    vscode.window.showInformationMessage('Crowe Logic product icons applied.');
}
async function applyAll(_ctx) {
    await applyTheme();
    await applyProductIcons();
    applyTitleBar();
}
function applyTitleBar() {
    const cfg = vscode.workspace.getConfiguration();
    const desired = vscode.workspace
        .getConfiguration('croweLogic')
        .get('titleBarText', 'Crowe Logic — ${rootName}${separator}${activeEditorShort}');
    cfg.update('window.title', desired, vscode.ConfigurationTarget.Global);
}
// ── Auth (PAT in SecretStorage) ──────────────────────────────────
async function signIn(ctx) {
    const pat = await vscode.window.showInputBox({
        prompt: 'Paste your Crowe Logic PAT. Mint one at https://api.crowelogic.com/account',
        password: true,
        placeHolder: 'crowe_pat_...',
        ignoreFocusOut: true,
        validateInput: (v) => !v ? 'PAT cannot be empty'
            : v.startsWith('crowe_pat_') ? null
                : "PAT must start with 'crowe_pat_'",
    });
    if (!pat)
        return;
    await ctx.secrets.store(PAT_SECRET_KEY, pat.trim());
    vscode.window.showInformationMessage('Crowe Logic: signed in.');
}
async function signOut(ctx) {
    await ctx.secrets.delete(PAT_SECRET_KEY);
    vscode.window.showInformationMessage('Crowe Logic: signed out.');
}
function getFoundryConfig() {
    const cfg = vscode.workspace.getConfiguration('croweLogic');
    return {
        baseURL: (cfg.get('foundryBaseURL') || '').trim().replace(/\/+$/, ''),
        model: cfg.get('model') || 'auto',
    };
}
// ── Chat participant ─────────────────────────────────────────────
function registerChatParticipant(ctx) {
    const chatApi = vscode.chat;
    if (!chatApi || typeof chatApi.createChatParticipant !== 'function') {
        return;
    }
    const participant = chatApi.createChatParticipant('crowe-logic.agent', async (request, ctxReq, stream, token) => {
        const pat = await ctx.secrets.get(PAT_SECRET_KEY);
        const cfg = getFoundryConfig();
        // Foundry path: signed in and base URL configured.
        if (pat && cfg.baseURL) {
            try {
                await runFoundryTurn({
                    baseURL: cfg.baseURL,
                    model: cfg.model,
                    pat,
                    request,
                    history: ctxReq?.history ?? [],
                    stream,
                    token,
                });
                return {};
            }
            catch (err) {
                // Surface the error and fall through to Copilot fallback if
                // available. Network blips shouldn't leave the user unable to
                // ask anything at all.
                const msg = err instanceof Error ? err.message : String(err);
                stream.markdown(`\n\n_Foundry unavailable (${msg}); falling back._\n\n`);
            }
        }
        // Copilot fallback so the participant is still useful when not
        // signed in or when the foundry is unreachable.
        const lm = vscode.lm;
        if (lm?.selectChatModels) {
            const models = await lm.selectChatModels({ vendor: 'copilot' });
            if (models?.length) {
                await runCopilotTurn({ model: models[0], request, stream, token });
                if (!pat) {
                    stream.markdown('\n\n---\n_Tip: run **Crowe Logic: Sign In** to route through your CroweLM stack._\n');
                }
                return {};
            }
        }
        // Last resort: tell the user how to sign in.
        stream.markdown('**Crowe Logic is not signed in.**\n\n' +
            'Run `Crowe Logic: Sign In` from the command palette and paste a PAT minted at ' +
            'https://api.crowelogic.com/account.\n');
        return {};
    });
    participant.iconPath = {
        light: vscode.Uri.joinPath(ctx.extensionUri, 'media', 'crowe-logic-avatar-light.png'),
        dark: vscode.Uri.joinPath(ctx.extensionUri, 'media', 'crowe-logic-avatar-dark.png'),
    };
    ctx.subscriptions.push(participant);
}
function buildMessagesFromHistory(history, currentPrompt) {
    const messages = [];
    for (const turn of history ?? []) {
        if (typeof turn?.prompt === 'string' && turn.prompt) {
            messages.push({ role: 'user', content: turn.prompt });
            continue;
        }
        if (Array.isArray(turn?.response)) {
            // ChatResponseTurn parts have varying shapes across VS Code versions;
            // best-effort text extraction.
            const text = turn.response
                .map((part) => {
                const v = part?.value;
                if (typeof v === 'string')
                    return v;
                if (typeof v?.value === 'string')
                    return v.value;
                return '';
            })
                .filter((s) => s)
                .join('');
            if (text)
                messages.push({ role: 'assistant', content: text });
        }
    }
    messages.push({ role: 'user', content: currentPrompt });
    return messages;
}
async function runFoundryTurn(opts) {
    const { baseURL, model, pat, request, history, stream, token } = opts;
    const controller = new AbortController();
    const cancelSub = token.onCancellationRequested(() => controller.abort());
    const messages = buildMessagesFromHistory(history, String(request?.prompt ?? ''));
    let res;
    try {
        res = await fetch(`${baseURL}/api/gateway/chat/stream`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                Authorization: `Bearer ${pat}`,
                Accept: 'text/event-stream',
            },
            body: JSON.stringify({ messages, model }),
            signal: controller.signal,
        });
    }
    catch (err) {
        cancelSub.dispose();
        throw err;
    }
    if (!res.ok || !res.body) {
        cancelSub.dispose();
        const text = await res.text().catch(() => '');
        throw new Error(`HTTP ${res.status}${text ? `: ${text.slice(0, 240)}` : ''}`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buf = '';
    try {
        while (true) {
            const { value, done } = await reader.read();
            if (done)
                break;
            buf += decoder.decode(value, { stream: true });
            // SSE record terminator is a blank line. Records contain one or
            // more `field: value` lines; we only care about `data:` here.
            let idx;
            while ((idx = buf.indexOf('\n\n')) !== -1) {
                const raw = buf.slice(0, idx);
                buf = buf.slice(idx + 2);
                const event = parseSseRecord(raw);
                if (event)
                    handleFoundryEvent(event, stream);
            }
        }
    }
    finally {
        cancelSub.dispose();
        try {
            reader.releaseLock();
        }
        catch { /* ignore */ }
    }
}
function parseSseRecord(raw) {
    let dataStr = '';
    for (const line of raw.split('\n')) {
        if (line.startsWith('data:')) {
            dataStr += line.slice(5).trimStart();
        }
    }
    if (!dataStr)
        return null;
    try {
        const payload = JSON.parse(dataStr);
        return { type: typeof payload?.type === 'string' ? payload.type : 'unknown', payload };
    }
    catch {
        return null;
    }
}
function handleFoundryEvent(event, stream) {
    const p = event.payload ?? {};
    switch (event.type) {
        case 'token':
            if (typeof p.delta === 'string')
                stream.markdown(p.delta);
            return;
        case 'reasoning':
            // crowe-stream v0 reasoning isn't first-class in VS Code's chat
            // surface; render as a muted aside so power users can see thought
            // traces from thinking models without it being mistaken for the
            // assistant's final answer.
            if (typeof p.delta === 'string' && stream.progress) {
                stream.progress(p.delta);
            }
            return;
        case 'tool': {
            const status = p.status === 'ok' ? '' : ` (${p.status ?? 'unknown'})`;
            const dur = typeof p.duration_ms === 'number' ? ` · ${p.duration_ms}ms` : '';
            stream.markdown(`\n\n> *Tool: ${p.name ?? 'unknown'}${status}${dur}*\n\n`);
            return;
        }
        case 'spinner':
            if (typeof p.label === 'string' && p.label && stream.progress) {
                stream.progress(p.label);
            }
            return;
        case 'error':
            stream.markdown(`\n\n**Error (${p.kind ?? 'unknown'}):** ${p.message ?? 'unknown failure'}\n`);
            return;
        case 'ready':
        case 'segment_end':
        case 'done':
        default:
            return;
    }
}
// ── Copilot fallback (preserves existing behavior) ───────────────
async function runCopilotTurn(opts) {
    const { model, request, stream, token } = opts;
    const messages = [
        vscode.LanguageModelChatMessage.User(SYSTEM_PROMPT),
        vscode.LanguageModelChatMessage.User(String(request?.prompt ?? '')),
    ];
    const response = await model.sendRequest(messages, {}, token);
    for await (const fragment of response.text) {
        stream.markdown(fragment);
    }
}
//# sourceMappingURL=extension.js.map