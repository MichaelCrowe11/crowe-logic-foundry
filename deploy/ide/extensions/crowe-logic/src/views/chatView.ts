/**
 * Dedicated Crowe Logic chat webview.
 *
 * Lives inside the `crowe-logic` activity-bar container alongside the
 * Plan and Tool Activity tree views. Provides a fully branded chat
 * surface — gold-on-graphite, Crowe Logic mark, no Microsoft chrome —
 * that talks directly to the headless Foundry runner. This is the
 * equivalent of Claude Code's dedicated panel: the user never has to
 * touch the shared `workbench.action.chat.open` surface unless they
 * want to.
 *
 * Wire-up (extension.ts):
 *
 *   const chat = new CroweChatViewProvider(context);
 *   ctx.subscriptions.push(
 *     vscode.window.registerWebviewViewProvider(
 *       CroweChatViewProvider.VIEW_ID,
 *       chat,
 *       { webviewOptions: { retainContextWhenHidden: true } },
 *     ),
 *   );
 */

import * as vscode from 'vscode';
import { runFoundryTurn, FoundryMessage, FoundryEvent } from '../agent';
import { resolveFoundryPath, resolvePythonPath } from '../resolvePaths';

interface IncomingMessage {
    type: 'send' | 'reset' | 'cancel' | 'pickModel' | 'getModel';
    prompt?: string;
}

interface OutgoingMessage {
    type:
        | 'ready'
        | 'token'
        | 'reasoning'
        | 'tool'
        | 'error'
        | 'done'
        | 'identity'
        | 'cleared'
        | 'modelChanged'
        | 'fileContext';
    [k: string]: unknown;
}

export class CroweChatViewProvider implements vscode.WebviewViewProvider {
    public static readonly VIEW_ID = 'crowe-logic.chat';

    private view: vscode.WebviewView | undefined;
    private cancellation: vscode.CancellationTokenSource | undefined;
    private history: FoundryMessage[] = [];

    constructor(private readonly context: vscode.ExtensionContext) {}

    resolveWebviewView(view: vscode.WebviewView): void {
        this.view = view;

        view.webview.options = {
            enableScripts: true,
            localResourceRoots: [
                vscode.Uri.joinPath(this.context.extensionUri, 'media'),
            ],
        };

        view.webview.html = this.renderHtml(view.webview);

        view.webview.onDidReceiveMessage((msg: IncomingMessage) => {
            switch (msg.type) {
                case 'send':
                    if (msg.prompt && msg.prompt.trim()) {
                        void this.handleTurn(msg.prompt.trim());
                    }
                    return;
                case 'reset':
                    this.history = [];
                    this.post({ type: 'cleared' });
                    return;
                case 'cancel':
                    this.cancellation?.cancel();
                    return;
                case 'pickModel':
                    void vscode.commands.executeCommand('crowe-logic.pickModel');
                    return;
                case 'getModel':
                    this.postCurrentModel();
                    return;
            }
        });

        // Refresh model display when settings change
        const modelChangeSub = vscode.workspace.onDidChangeConfiguration((e) => {
            if (e.affectsConfiguration('croweLogic.model')) {
                this.postCurrentModel();
            }
        });

        // Refresh file context when the active editor changes
        const editorChangeSub = vscode.window.onDidChangeActiveTextEditor(() => {
            this.postFileContext();
        });
        view.onDidDispose(() => {
            modelChangeSub.dispose();
            editorChangeSub.dispose();
        });

        // Send identity + theme tokens once the webview is up.
        view.onDidChangeVisibility(() => {
            if (view.visible) {
                this.post({
                    type: 'identity',
                    name: 'Crowe Logic',
                    tagline: 'Universal AI agent · CroweLM stack',
                });
                this.postCurrentModel();
                this.postFileContext();
            }
        });
    }

    private postCurrentModel(): void {
        const cfg = vscode.workspace.getConfiguration('croweLogic');
        const model = cfg.get<string>('model') || 'auto';
        const display = model === 'auto' ? 'Auto' : model;
        this.post({ type: 'modelChanged', model: display });
    }

    private postFileContext(): void {
        const editor = vscode.window.activeTextEditor;
        if (editor && editor.document.uri.scheme === 'file') {
            const fsPath = editor.document.uri.fsPath;
            const wsPath = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
            const rel = wsPath && fsPath.startsWith(wsPath) ? fsPath.slice(wsPath.length + 1) : fsPath.split('/').slice(-2).join('/');
            this.post({ type: 'fileContext', file: rel });
        } else {
            this.post({ type: 'fileContext', file: null });
        }
    }

    private post(msg: OutgoingMessage): void {
        this.view?.webview.postMessage(msg);
    }

    private async handleTurn(prompt: string): Promise<void> {
        const cfg = vscode.workspace.getConfiguration('croweLogic');
        const model = cfg.get<string>('model') || 'auto';
        const sessionId = `crowe-chat-${Math.floor(Date.now() / 1000)}`;

        const foundryPath = resolveFoundryPath();
        const pythonPath = foundryPath ? resolvePythonPath(foundryPath) : null;

        if (!foundryPath || !pythonPath) {
            this.post({
                type: 'error',
                message:
                    'Foundry not found. Set `croweLogic.foundryPath` and `croweLogic.pythonPath` in settings.',
                kind: 'config',
            });
            return;
        }

        this.history.push({ role: 'user', content: prompt });

        this.cancellation?.dispose();
        this.cancellation = new vscode.CancellationTokenSource();

        try {
            // Active workspace folder is the cwd for the agent's tools,
            // so filesystem/shell/git operate on the user's open project,
            // not on the Foundry checkout itself.
            const workspaceFolder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
            const events = runFoundryTurn(
                { messages: this.history, model, session: sessionId },
                {
                    pythonPath,
                    foundryPath,
                    workspaceFolder,
                    cancellation: this.cancellation.token,
                },
            );

            let assistantText = '';
            for await (const evt of events as AsyncGenerator<FoundryEvent>) {
                if (this.cancellation.token.isCancellationRequested) break;
                this.dispatchEvent(evt);
                if (evt.type === 'token' && typeof evt.delta === 'string') {
                    assistantText += evt.delta;
                }
                if (evt.type === 'done') break;
            }

            if (assistantText) {
                this.history.push({ role: 'assistant', content: assistantText });
            }
        } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            this.post({ type: 'error', message: msg, kind: 'runtime' });
        }
    }

    private dispatchEvent(evt: FoundryEvent): void {
        switch (evt.type) {
            case 'ready':
                this.post({ type: 'ready' });
                return;
            case 'token':
                this.post({ type: 'token', delta: evt.delta });
                return;
            case 'reasoning':
                this.post({ type: 'reasoning', delta: evt.delta });
                return;
            case 'tool':
                this.post({
                    type: 'tool',
                    name: evt.name,
                    status: evt.status,
                    duration_ms: evt.duration_ms,
                    args: evt.args,
                    result: evt.result,
                });
                return;
            case 'error':
                this.post({
                    type: 'error',
                    message: evt.message ?? 'unknown failure',
                    kind: evt.kind ?? 'unknown',
                });
                return;
            case 'done':
                this.post({
                    type: 'done',
                    tokens: evt.tokens,
                    elapsed_ms: evt.elapsed_ms,
                });
                return;
            default:
                return;
        }
    }

    private renderHtml(webview: vscode.Webview): string {
        return renderChatHtml(webview, this.context.extensionUri);
    }
}

/**
 * Build the chat HTML. Exported so editor-area webview panels (commands)
 * can reuse the exact same renderer the sidebar view uses.
 */
export function renderChatHtml(webview: vscode.Webview, extensionUri: vscode.Uri): string {
        // Two distinct asset roles, modelled on how GitHub does Copilot's
        // mark + Copilot's character avatar:
        //
        //   diamond mark (mark.svg, 1.4 KB vector)
        //     - Participant icon in the chat panel header
        //     - Activity-bar glyph
        //     - Anywhere a small, monochromatic brand identity is needed
        //
        //   face avatar (face-dark.png / face-light.png, 256x256 portrait)
        //     - The Crowe Logic AI character avatar shown next to every
        //       assistant message bubble in the chat webview
        //     - Read-large surfaces where personality matters
        //
        // The croweLogic.chatPersona setting lets users force either one
        // for the chat bubble. Default is 'face' so personality shows by
        // default; users who want a quieter UI can pick 'mark'.
        const persona = vscode.workspace.getConfiguration('croweLogic')
            .get<string>('chatPersona', 'face');
        const bubbleAsset = persona === 'mark' ? 'avatar-dark.png' : 'face-dark.png';
        const avatarUri = webview.asWebviewUri(
            vscode.Uri.joinPath(extensionUri, 'media', bubbleAsset),
        );
        const titlebarUri = webview.asWebviewUri(
            vscode.Uri.joinPath(extensionUri, 'media', 'mark.svg'),
        );
        const nonce = Math.random().toString(36).slice(2);

        // Brand tokens. Mirror cli/branding.py palette so terminal + chat
        // render in the same colors.
        return /* html */ `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${webview.cspSource} data:; style-src 'unsafe-inline' ${webview.cspSource}; script-src 'nonce-${nonce}';">
<style>
  :root {
    --gold: #bfa669;
    --gold-bright: #d8c089;
    --gold-deep: #9c8451;
    --gold-faint: rgba(191,166,105,0.10);
    --graphite: #0b0b0c;
    --panel: #121214;
    --panel-2: #1a1815;
    --line: #262527;
    --line-soft: #1a1a1c;
    --parchment: #e8e2cf;
    --parchment-dim: rgba(232,226,207,0.65);
    --muted: #948b72;
    --error: #d97757;
    --success: #6fbf73;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0; height: 100%;
    background: var(--graphite); color: var(--parchment);
    font: 13.5px/1.6 -apple-system, BlinkMacSystemFont, "SF Pro Text", "Inter", sans-serif;
    -webkit-font-smoothing: antialiased;
    overflow: hidden;
  }
  #app { display: flex; flex-direction: column; height: 100%; }

  header {
    display: flex; align-items: center; gap: 10px;
    padding: 10px 14px;
    border-bottom: 1px solid var(--line);
    background: var(--graphite);
    flex-shrink: 0;
  }
  header img.brand-avatar {
    width: 22px; height: 22px; border-radius: 50%;
    box-shadow: 0 0 0 1px var(--gold-deep);
  }
  header .title {
    font-weight: 600; font-size: 12.5px; letter-spacing: 0.01em;
    color: var(--parchment);
  }
  header .title em {
    color: var(--gold); font-style: normal; font-weight: 600;
  }
  header .actions { margin-left: auto; display: flex; gap: 2px; }
  header button.icon-btn {
    background: transparent; border: 0; color: var(--muted);
    padding: 5px 9px; border-radius: 5px; cursor: pointer;
    font-size: 11px; font-weight: 500; letter-spacing: 0.01em;
    transition: color 120ms, background 120ms;
  }
  header button.icon-btn:hover { color: var(--gold); background: var(--panel-2); }

  /* Model selector strip — always-visible row beneath the header.
     Uses a subtle moving gradient on the bottom border to feel alive. */
  .model-bar {
    display: flex; align-items: center; gap: 10px;
    padding: 8px 14px;
    background: var(--panel);
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
    font-size: 11.5px; flex-shrink: 0;
    position: relative;
  }
  .model-bar::after {
    content: ''; position: absolute; left: 0; right: 0; bottom: 0;
    height: 1px;
    background: linear-gradient(90deg,
      transparent 0%,
      rgba(191,166,105,0.15) 25%,
      rgba(191,166,105,0.45) 50%,
      rgba(191,166,105,0.15) 75%,
      transparent 100%);
    background-size: 200% 100%;
    animation: shimmerLine 6s ease-in-out infinite;
  }
  @keyframes shimmerLine {
    0%,100% { background-position: 100% 0; opacity: 0.5; }
    50% { background-position: -100% 0; opacity: 1; }
  }
  .model-bar .model-pill {
    display: inline-flex; align-items: center; gap: 7px;
    padding: 4px 10px 4px 8px;
    background: var(--panel-2);
    border: 1px solid var(--line);
    border-radius: 999px;
    color: var(--parchment);
    cursor: pointer;
    transition: border-color 120ms, background 120ms;
    font-weight: 500;
  }
  .model-bar .model-pill:hover {
    border-color: var(--gold-deep);
    background: rgba(191,166,105,0.08);
  }
  .model-bar .model-pill .dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--gold);
    box-shadow: 0 0 6px var(--gold);
  }
  .model-bar .model-pill .chevron {
    color: var(--muted); font-size: 9px; margin-left: 2px;
  }
  .model-bar .meta-stat {
    color: var(--muted); font-family: "SF Mono", ui-monospace, Menlo, monospace;
    font-size: 10.5px; letter-spacing: 0.02em;
    margin-left: auto;
  }

  #log {
    flex: 1; overflow-y: auto;
    padding: 20px 18px 12px;
    scroll-behavior: smooth;
    position: relative;
  }
  /* Floating "scroll to bottom" button — shows only when scrolled up
     and a stream is active. Restores follow-the-stream UX without
     fighting users who want to read history. */
  .scroll-to-bottom {
    position: absolute; bottom: 110px; right: 16px;
    width: 32px; height: 32px; border-radius: 50%;
    background: var(--gold);
    color: var(--graphite);
    border: 0; cursor: pointer;
    display: none; align-items: center; justify-content: center;
    box-shadow: 0 4px 16px rgba(191,166,105,0.30);
    font-size: 12px; font-weight: 700;
    z-index: 10;
    transition: transform 80ms, opacity 160ms;
    opacity: 0;
  }
  .scroll-to-bottom.visible {
    display: flex; opacity: 1;
  }
  .scroll-to-bottom:hover { background: var(--gold-bright); }
  .scroll-to-bottom:active { transform: scale(0.92); }

  /* File-context strip — appears above the composer when a workspace
     file is open in the editor. Subtle hint that the agent has access.
     Set via host messages (file changes detected on the extension side). */
  .file-context {
    border-top: 1px solid var(--line);
    padding: 6px 14px;
    background: var(--panel);
    font-size: 10.5px;
    color: var(--muted);
    display: none; align-items: center; gap: 8px;
    flex-shrink: 0;
  }
  .file-context.visible { display: flex; }
  .file-context::before {
    content: ''; width: 6px; height: 6px; border-radius: 50%;
    background: var(--gold); flex-shrink: 0;
  }
  .file-context strong {
    color: var(--gold); font-weight: 500;
    font-family: "SF Mono", ui-monospace, Menlo, monospace;
    font-size: 10.5px;
  }

  /* Suggested follow-ups: chips that appear after a completed assistant
     turn so users can drill in without retyping. Static for now;
     dynamic suggestions land when the agent emits "followups" events. */
  .followups {
    margin-top: 8px;
    display: flex; flex-wrap: wrap; gap: 6px;
  }
  .followup-chip {
    padding: 4px 10px; border-radius: 999px;
    background: rgba(191,166,105,0.06);
    border: 1px solid rgba(191,166,105,0.20);
    color: var(--gold); cursor: pointer;
    font-size: 11.5px; font-weight: 500;
    transition: background 120ms, border-color 120ms;
  }
  .followup-chip:hover {
    background: rgba(191,166,105,0.14);
    border-color: var(--gold-deep);
  }
  #log::-webkit-scrollbar { width: 8px; }
  #log::-webkit-scrollbar-track { background: transparent; }
  #log::-webkit-scrollbar-thumb { background: rgba(156,132,81,0.25); border-radius: 4px; }
  #log::-webkit-scrollbar-thumb:hover { background: rgba(156,132,81,0.5); }

  .empty {
    display: flex; flex-direction: column; align-items: center;
    padding: 32px 18px 16px;
    text-align: center;
    position: relative;
  }
  .empty::before {
    content: ''; position: absolute; top: 8px; left: 50%;
    transform: translateX(-50%);
    width: 220px; height: 220px;
    background: radial-gradient(circle, rgba(191,166,105,0.10) 0%, transparent 65%);
    pointer-events: none;
    animation: empty-pulse 4s ease-in-out infinite;
    z-index: 0;
  }
  @keyframes empty-pulse {
    0%,100% { opacity: 0.5; transform: translateX(-50%) scale(1); }
    50% { opacity: 1; transform: translateX(-50%) scale(1.08); }
  }
  .empty > * { position: relative; z-index: 1; }
  .empty img {
    width: 44px; height: 44px; margin-bottom: 14px; border-radius: 50%;
    box-shadow: 0 0 0 1.5px var(--gold-deep), 0 4px 14px rgba(191,166,105,0.20);
    animation: avatarFloat 3.5s ease-in-out infinite;
  }
  @keyframes avatarFloat {
    0%,100% { transform: translateY(0); }
    50% { transform: translateY(-3px); }
  }
  .empty h2 {
    font-size: 17px; color: var(--parchment); margin: 0 0 6px;
    font-weight: 600; letter-spacing: -0.01em;
  }
  .empty p {
    font-size: 12.5px; margin: 0; max-width: 320px; line-height: 1.55;
    color: var(--muted);
  }
  .examples {
    width: 100%; max-width: 420px; margin: 22px auto 0;
    display: grid; grid-template-columns: 1fr 1fr; gap: 8px;
  }
  .example-card {
    text-align: left;
    padding: 10px 12px;
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 8px;
    cursor: pointer;
    transition: border-color 120ms, background 120ms, transform 80ms;
    color: var(--parchment);
    font-size: 12px;
  }
  .example-card:hover {
    border-color: var(--gold-deep);
    background: var(--panel-2);
  }
  .example-card:active { transform: scale(0.99); }
  .example-card .ec-eyebrow {
    color: var(--gold);
    font-size: 9.5px; font-weight: 600;
    letter-spacing: 0.12em; text-transform: uppercase;
    margin-bottom: 3px;
  }
  .example-card .ec-text {
    color: var(--parchment); line-height: 1.4;
  }

  .turn {
    margin-bottom: 18px; position: relative;
    animation: turnSlideIn 320ms cubic-bezier(0.2, 0.7, 0.2, 1);
  }
  @keyframes turnSlideIn {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: translateY(0); }
  }
  .turn .role {
    display: flex; align-items: center; gap: 8px;
    font-size: 11px; font-weight: 600;
    color: var(--muted); letter-spacing: 0.04em;
    margin-bottom: 6px;
  }
  .turn.user .role { color: var(--gold-deep); }
  .turn.assistant .role { color: var(--gold); }
  .turn .role .role-name { font-size: 11.5px; font-weight: 600; }
  .turn .role img {
    width: 18px; height: 18px; border-radius: 50%;
    box-shadow: 0 0 0 1px var(--gold-deep);
  }
  .turn .role .you-circle {
    width: 18px; height: 18px; border-radius: 50%;
    background: var(--panel-2); border: 1px solid var(--gold-deep);
    color: var(--gold); display: flex; align-items: center; justify-content: center;
    font-size: 9px; font-weight: 700; letter-spacing: 0;
  }
  .turn-actions {
    position: absolute; top: -2px; right: 0;
    display: flex; gap: 2px;
    opacity: 0; transition: opacity 120ms;
  }
  .turn:hover .turn-actions { opacity: 1; }
  .turn-actions button {
    background: var(--panel-2); border: 1px solid var(--line);
    color: var(--muted); font-size: 10.5px;
    padding: 3px 8px; border-radius: 5px; cursor: pointer;
    transition: color 120ms, border-color 120ms;
  }
  .turn-actions button:hover { color: var(--gold); border-color: var(--gold-deep); }

  .bubble {
    padding: 11px 14px; border-radius: 10px;
    border: 1px solid var(--line);
    word-break: break-word;
    font-size: 13.5px; line-height: 1.6;
  }
  .turn.user .bubble {
    background: var(--panel-2);
    border-color: rgba(156,132,81,0.30);
    color: var(--parchment);
    white-space: pre-wrap;
  }
  .turn.assistant .bubble {
    background: var(--panel);
    border-color: var(--line);
    color: var(--parchment);
    position: relative;
  }
  /* Streaming bubble: subtle gold border that pulses */
  .turn.assistant .bubble.streaming {
    border-color: rgba(191,166,105,0.30);
    box-shadow: 0 0 0 1px rgba(191,166,105,0.05),
                0 4px 24px rgba(191,166,105,0.06);
    animation: streamingGlow 2.4s ease-in-out infinite;
  }
  @keyframes streamingGlow {
    0%,100% { border-color: rgba(191,166,105,0.30); box-shadow: 0 0 0 1px rgba(191,166,105,0.05), 0 4px 18px rgba(191,166,105,0.04); }
    50% { border-color: rgba(216,192,137,0.55); box-shadow: 0 0 0 1px rgba(216,192,137,0.10), 0 6px 28px rgba(216,192,137,0.10); }
  }

  /* Markdown rendering inside assistant bubbles */
  .bubble p { margin: 0 0 10px; }
  .bubble p:last-child { margin-bottom: 0; }
  .bubble h1, .bubble h2, .bubble h3 {
    margin: 14px 0 8px; font-weight: 600; letter-spacing: -0.01em;
    color: var(--parchment);
  }
  .bubble h1 { font-size: 18px; color: var(--gold); }
  .bubble h2 { font-size: 16px; color: var(--gold-bright); }
  .bubble h3 { font-size: 14px; }
  .bubble ul, .bubble ol { margin: 0 0 10px; padding-left: 22px; }
  .bubble li { margin: 3px 0; }
  .bubble ul li::marker { color: var(--gold); }
  .bubble ol li::marker { color: var(--gold-deep); }
  .bubble strong { color: var(--parchment); font-weight: 600; }
  .bubble em { color: var(--gold-bright); font-style: italic; }
  .bubble a { color: var(--gold); text-decoration: underline; text-decoration-color: var(--gold-deep); }
  .bubble a:hover { color: var(--gold-bright); }
  .bubble code.inline {
    font-family: "SF Mono", ui-monospace, Menlo, monospace;
    background: var(--panel-2); color: var(--gold-bright);
    padding: 1px 6px; border-radius: 4px; font-size: 12px;
    border: 1px solid var(--line);
  }
  .bubble pre {
    background: #07070a; border: 1px solid var(--line);
    border-radius: 8px; padding: 0; margin: 10px 0;
    overflow: hidden;
    position: relative;
  }
  .bubble pre .code-header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 6px 12px;
    background: rgba(191,166,105,0.05);
    border-bottom: 1px solid var(--line);
    font-size: 10.5px; color: var(--gold);
    text-transform: uppercase; letter-spacing: 0.10em;
    font-weight: 600;
    font-family: "SF Mono", ui-monospace, Menlo, monospace;
  }
  .bubble pre .code-copy {
    background: transparent; border: 0; color: var(--muted);
    cursor: pointer; padding: 2px 6px; border-radius: 3px;
    font-size: 10px; transition: color 120ms;
    text-transform: none; letter-spacing: 0;
  }
  .bubble pre .code-copy:hover { color: var(--gold); }
  .bubble pre code {
    display: block;
    font-family: "SF Mono", ui-monospace, Menlo, monospace;
    color: var(--parchment); padding: 12px 14px;
    overflow-x: auto;
    font-size: 12px; line-height: 1.6;
  }
  /* Minimal regex syntax color — keywords, strings, comments, numbers */
  .bubble pre code .kw { color: #c08fcf; }
  .bubble pre code .str { color: #d0a060; }
  .bubble pre code .com { color: #6c6452; font-style: italic; }
  .bubble pre code .num { color: #9fb87a; }
  .bubble pre code .fn { color: #82a0c0; }
  .bubble blockquote {
    border-left: 3px solid var(--gold-deep);
    padding: 4px 0 4px 12px; margin: 10px 0; color: var(--muted);
    font-style: italic;
  }
  .bubble hr { border: 0; border-top: 1px solid var(--line); margin: 14px 0; }

  /* Streaming cursor — only when we have text */
  .bubble.streaming.has-text::after {
    content: '▌'; color: var(--gold);
    animation: blink 1s step-start infinite;
    margin-left: 1px;
  }
  @keyframes blink { 50% { opacity: 0; } }

  /* Typing-dots placeholder — shown while bubble is streaming but empty.
     ChatGPT/Claude-style 3 dots that scale in sequence. */
  .bubble.streaming:not(.has-text) {
    min-height: 28px;
  }
  .bubble.streaming:not(.has-text)::before {
    content: '';
    display: inline-flex; gap: 5px;
    height: 8px;
    background-image:
      radial-gradient(circle at 4px 50%, var(--gold) 4px, transparent 4px),
      radial-gradient(circle at 16px 50%, var(--gold) 4px, transparent 4px),
      radial-gradient(circle at 28px 50%, var(--gold) 4px, transparent 4px);
    background-repeat: no-repeat;
    width: 36px; height: 8px;
    animation: typingDots 1.2s ease-in-out infinite;
  }
  @keyframes typingDots {
    0%   { opacity: 0.30; }
    33%  { opacity: 0.55; }
    66%  { opacity: 0.85; }
    100% { opacity: 0.30; }
  }
  /* Each dot pulses with a slight stagger via background-size animation */
  @keyframes typingDot1 { 0%,80%,100% { transform: translateY(0); opacity: 0.4; } 40% { transform: translateY(-3px); opacity: 1; } }

  /* Reasoning — collapsible chevron pattern */
  details.reasoning {
    margin: 0 0 8px; border: 1px solid var(--line);
    border-radius: 8px; background: rgba(191,166,105,0.025);
    overflow: hidden;
  }
  details.reasoning[open] {
    background: rgba(191,166,105,0.045);
    border-color: rgba(191,166,105,0.20);
  }
  details.reasoning > summary {
    list-style: none; cursor: pointer;
    padding: 8px 12px; font-size: 11px;
    color: var(--gold); letter-spacing: 0.06em;
    font-weight: 600;
    display: flex; align-items: center; gap: 8px;
    user-select: none;
  }
  details.reasoning > summary::-webkit-details-marker { display: none; }
  details.reasoning > summary::before {
    content: '▸'; color: var(--gold-deep); font-size: 9px;
    transition: transform 120ms;
    display: inline-block; width: 8px;
  }
  details.reasoning[open] > summary::before { transform: rotate(90deg); }
  details.reasoning > summary:hover { color: var(--gold-bright); }
  details.reasoning .reasoning-pulse {
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--gold);
    animation: reasoningPulse 1.4s ease-in-out infinite;
  }
  @keyframes reasoningPulse {
    0%,100% { opacity: 0.3; transform: scale(0.85); }
    50% { opacity: 1; transform: scale(1.1); }
  }
  details.reasoning .reasoning-body {
    padding: 4px 14px 12px; font-size: 11.5px; line-height: 1.6;
    color: var(--muted); white-space: pre-wrap; max-height: 320px;
    overflow-y: auto;
  }

  /* Tools — pill-shaped, status-color-coded, click to expand */
  .tool {
    display: inline-flex; align-items: center; gap: 8px;
    margin: 0;
    padding: 5px 11px; border-radius: 999px;
    font-family: "SF Mono", ui-monospace, Menlo, monospace;
    font-size: 11px; font-weight: 500;
    background: var(--gold-faint); color: var(--gold);
    border: 1px solid rgba(191,166,105,0.25);
    cursor: help;
    transition: border-color 120ms, background 120ms;
  }
  .tool:hover {
    border-color: var(--gold-deep);
    background: rgba(191,166,105,0.16);
  }
  .tool.error { background: rgba(217,119,87,0.10); color: var(--error); border-color: rgba(217,119,87,0.30); }
  .tool.error:hover { border-color: var(--error); }
  .tool .tool-status {
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--gold);
  }
  .tool.error .tool-status { background: var(--error); }
  .tool.ok .tool-status { background: var(--success); }
  .tool.running .tool-status {
    animation: toolPulse 1s ease-in-out infinite;
  }
  @keyframes toolPulse {
    0%,100% { opacity: 0.4; }
    50% { opacity: 1; }
  }
  .tool .duration {
    color: var(--muted); font-weight: 400; margin-left: 2px;
    font-size: 10px;
  }

  .tool-row {
    display: flex; flex-wrap: wrap; gap: 6px;
    margin: 6px 0 8px;
  }
  .tool-row .tool-label {
    font-size: 10px; color: var(--muted);
    text-transform: uppercase; letter-spacing: 0.10em;
    font-weight: 600;
    width: 100%;
    margin-bottom: 2px;
  }

  .error-banner {
    background: rgba(217,119,87,0.08); border: 1px solid var(--error);
    color: var(--error); padding: 9px 12px; border-radius: 8px;
    font-size: 12.5px; margin-top: 8px;
  }
  .meta {
    font-size: 10.5px; color: var(--muted); margin-top: 6px;
    letter-spacing: 0.06em;
  }

  form#composer {
    border-top: 1px solid var(--line);
    background: var(--panel);
    padding: 12px 14px 14px;
    display: flex; gap: 8px; align-items: flex-end;
    flex-shrink: 0;
    position: relative;
  }
  textarea#prompt {
    flex: 1; resize: none; max-height: 240px; min-height: 40px;
    background: var(--panel-2); color: var(--parchment);
    border: 1px solid var(--line); border-radius: 10px;
    padding: 11px 14px; font: inherit; outline: none;
    transition: border-color 120ms, box-shadow 120ms;
  }
  textarea#prompt:focus {
    border-color: var(--gold);
    box-shadow: 0 0 0 2px rgba(191,166,105,0.18);
  }
  textarea#prompt::placeholder { color: var(--muted); }
  button.send-btn {
    background: linear-gradient(135deg, var(--gold-bright) 0%, var(--gold) 50%, var(--gold-deep) 100%);
    color: var(--graphite);
    border: 0; border-radius: 10px;
    width: 40px; height: 40px;
    cursor: pointer;
    transition: transform 80ms, box-shadow 160ms;
    flex-shrink: 0;
    display: flex; align-items: center; justify-content: center;
    font-size: 16px; font-weight: 700;
    box-shadow: 0 2px 8px rgba(191,166,105,0.25);
  }
  button.send-btn:hover {
    box-shadow: 0 4px 14px rgba(216,192,137,0.40);
  }
  button.send-btn:active { transform: scale(0.96); }
  button.send-btn:disabled {
    opacity: 0.4; cursor: not-allowed; box-shadow: none;
  }
  /* Stop button: graphite square with white stop-square inside, gold-deep
     border, subtle red accent only on hover. Reads "stop" without
     screaming, sits comfortably in the brand palette. */
  button.stop-btn {
    background: var(--panel-2);
    color: var(--parchment);
    border: 1px solid var(--gold-deep);
    border-radius: 10px;
    width: 40px; height: 40px;
    cursor: pointer; flex-shrink: 0;
    display: none; align-items: center; justify-content: center;
    font-size: 0;
    position: relative;
    transition: border-color 160ms, background 160ms, transform 80ms;
  }
  button.stop-btn::after {
    /* The actual "stop square" — a centered 12x12 white square */
    content: '';
    width: 12px; height: 12px;
    background: var(--parchment);
    border-radius: 2px;
    transition: background 160ms;
  }
  button.stop-btn:hover {
    border-color: var(--error);
    background: rgba(217,119,87,0.10);
  }
  button.stop-btn:hover::after { background: var(--error); }
  button.stop-btn:active { transform: scale(0.94); }
  body.busy button.stop-btn { display: flex; }
  body.busy button.send-btn { display: none; }
  body.busy textarea#prompt {
    opacity: 0.7;
    border-color: var(--line);
  }
  /* Streaming hint above composer with live elapsed time */
  .streaming-hint {
    position: absolute; top: -22px; left: 16px;
    font-size: 10.5px; color: var(--gold);
    letter-spacing: 0.06em;
    display: none; align-items: center; gap: 6px;
    background: var(--graphite);
    padding: 0 6px;
    font-variant-numeric: tabular-nums;
  }
  .streaming-hint::before {
    content: ''; width: 6px; height: 6px; border-radius: 50%;
    background: var(--gold); animation: toolPulse 1s ease-in-out infinite;
  }
  body.busy .streaming-hint { display: flex; }
  .streaming-hint .elapsed { color: var(--muted); margin-left: 4px; }
</style>
</head>
<body>
<div id="app">
  <header>
    <img class="brand-avatar" src="${titlebarUri}" alt="Crowe Logic" />
    <div class="title">Crowe Logic <em>Code</em></div>
    <div class="actions">
      <button class="icon-btn" id="reset" title="Clear conversation">New</button>
    </div>
  </header>
  <div class="model-bar">
    <button id="model-pill" class="model-pill" title="Change model">
      <span class="dot"></span>
      <span id="model-name">Auto</span>
      <span class="chevron">▾</span>
    </button>
    <span id="meta-stat" class="meta-stat"></span>
  </div>
  <div id="log-wrap">
    <button class="scroll-to-bottom" id="scroll-to-bottom" title="Scroll to latest">↓</button>
  <div id="log">
    <div class="empty">
      <img src="${avatarUri}" alt="" />
      <h2>How can Crowe Logic help?</h2>
      <p>A precision AI workstation. Ask anything: write code, plan a refactor, explain an error, design a system.</p>
      <div class="examples">
        <button class="example-card" data-prompt="Explain what this codebase does at a high level. Read the package.json, README, and the main entry point.">
          <div class="ec-eyebrow">Understand</div>
          <div class="ec-text">Explain this codebase to me</div>
        </button>
        <button class="example-card" data-prompt="Refactor the currently open file for readability. Preserve behavior. Show a diff before applying.">
          <div class="ec-eyebrow">Refactor</div>
          <div class="ec-text">Clean up the open file</div>
        </button>
        <button class="example-card" data-prompt="Write unit tests for the currently open file. Cover the happy path plus the edge cases I haven't thought of.">
          <div class="ec-eyebrow">Test</div>
          <div class="ec-text">Write tests for this file</div>
        </button>
        <button class="example-card" data-prompt="There's an error somewhere in this project. Help me find and fix it. Start by checking recent git changes and any failing tests.">
          <div class="ec-eyebrow">Debug</div>
          <div class="ec-text">Find and fix a bug</div>
        </button>
      </div>
    </div>
  </div>
  </div>
  <div class="file-context" id="file-context">
    Active file: <strong id="file-context-name">none</strong>
  </div>
  <form id="composer" autocomplete="off">
    <span class="streaming-hint" id="streaming-hint">Crowe Logic is thinking<span class="elapsed" id="elapsed">0.0s</span></span>
    <textarea
      id="prompt"
      rows="1"
      placeholder="Ask Crowe Logic..."
      spellcheck="false"></textarea>
    <button type="submit" id="send" class="send-btn" title="Send (Enter)">↑</button>
    <button type="button" id="cancel" class="stop-btn" title="Stop generating" aria-label="Stop"></button>
  </form>
</div>
<script nonce="${nonce}">
  const vscode = acquireVsCodeApi();
  const log = document.getElementById('log');
  const promptEl = document.getElementById('prompt');
  const composer = document.getElementById('composer');
  const cancelBtn = document.getElementById('cancel');
  const resetBtn = document.getElementById('reset');
  const modelPillEl = document.getElementById('model-pill');
  const modelNameEl = document.getElementById('model-name');
  const metaStatEl = document.getElementById('meta-stat');
  const body = document.body;
  const AVATAR_URI = '${avatarUri}';

  // Model pill — opens VS Code QuickPick via host
  modelPillEl.addEventListener('click', () => {
    vscode.postMessage({ type: 'pickModel' });
  });

  // Example prompt cards — fill the composer + send
  document.querySelectorAll('.example-card').forEach((el) => {
    el.addEventListener('click', () => {
      const prompt = el.getAttribute('data-prompt') || '';
      promptEl.value = prompt;
      promptEl.dispatchEvent(new Event('input'));
      promptEl.focus();
    });
  });

  // Code-block copy buttons (event delegation since blocks are dynamic)
  log.addEventListener('click', (e) => {
    const btn = e.target && e.target.classList && e.target.classList.contains('code-copy') ? e.target : null;
    if (!btn) return;
    const encoded = btn.getAttribute('data-code') || '';
    const code = decodeURIComponent(encoded);
    navigator.clipboard.writeText(code).then(() => {
      btn.textContent = 'Copied';
      setTimeout(() => { btn.textContent = 'Copy'; }, 1200);
    });
  });

  let currentTurnEl = null;
  let currentBubbleEl = null;
  let currentReasoningBody = null;
  let currentToolRow = null;
  let assistantBuffer = '';        // raw markdown accumulator
  let assistantRenderPending = false;

  // ── Minimal but capable Markdown renderer ────────────────────
  // Handles: headings, bold, italic, code (inline + fenced),
  // unordered + ordered lists, blockquotes, hr, links, line breaks.
  // Intentionally small; we'd swap to a real lib if requirements grow.
  function escapeHtml(s) {
    return s
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function renderMarkdown(src) {
    if (!src) return '';
    // Pull out fenced code blocks first so their contents don't get
    // mangled by the inline replacements below.
    const codeBlocks = [];
    src = src.replace(/\`\`\`(\\w*)\\n([\\s\\S]*?)\`\`\`/g, (_m, lang, body) => {
      const idx = codeBlocks.length;
      codeBlocks.push({ lang, body });
      return '\\u0000CODEBLOCK_' + idx + '\\u0000';
    });

    let html = '';
    const lines = src.split('\\n');
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];

      // Horizontal rule
      if (/^---+$/.test(line.trim())) { html += '<hr/>'; i++; continue; }

      // Headings
      const h = /^(#{1,3})\\s+(.+)$/.exec(line);
      if (h) {
        html += '<h' + h[1].length + '>' + inline(h[2]) + '</h' + h[1].length + '>';
        i++; continue;
      }

      // Blockquote
      if (/^>\\s/.test(line)) {
        let block = '';
        while (i < lines.length && /^>\\s?/.test(lines[i])) {
          block += lines[i].replace(/^>\\s?/, '') + '\\n';
          i++;
        }
        html += '<blockquote>' + inline(block.trim()) + '</blockquote>';
        continue;
      }

      // Unordered list
      if (/^[*\\-+]\\s+/.test(line)) {
        let items = '';
        while (i < lines.length && /^[*\\-+]\\s+/.test(lines[i])) {
          items += '<li>' + inline(lines[i].replace(/^[*\\-+]\\s+/, '')) + '</li>';
          i++;
        }
        html += '<ul>' + items + '</ul>';
        continue;
      }

      // Ordered list
      if (/^\\d+\\.\\s+/.test(line)) {
        let items = '';
        while (i < lines.length && /^\\d+\\.\\s+/.test(lines[i])) {
          items += '<li>' + inline(lines[i].replace(/^\\d+\\.\\s+/, '')) + '</li>';
          i++;
        }
        html += '<ol>' + items + '</ol>';
        continue;
      }

      // Paragraph (collect until blank line)
      if (line.trim() === '') { i++; continue; }
      let para = line;
      i++;
      while (i < lines.length && lines[i].trim() !== '' &&
             !/^(#{1,3}\\s|\\d+\\.\\s|[*\\-+]\\s|>\\s|---)/.test(lines[i])) {
        para += '\\n' + lines[i];
        i++;
      }
      html += '<p>' + inline(para).replace(/\\n/g, '<br/>') + '</p>';
    }

    // Restore code blocks with header (lang label + copy button) + minimal highlight
    html = html.replace(/\\u0000CODEBLOCK_(\\d+)\\u0000/g, (_m, idx) => {
      const cb = codeBlocks[+idx];
      const lang = (cb.lang || 'text').toLowerCase();
      const highlighted = highlightCode(cb.body.replace(/\\n$/, ''), lang);
      return '<pre>' +
        '<div class="code-header">' +
          '<span>' + escapeHtml(lang || 'code') + '</span>' +
          '<button class="code-copy" data-code="' + encodeURIComponent(cb.body.replace(/\\n$/, '')) + '">Copy</button>' +
        '</div>' +
        '<code class="lang-' + escapeHtml(lang) + '">' + highlighted + '</code>' +
      '</pre>';
    });

    return html;
  }

  // Tiny regex-based syntax highlighter. Not perfect, but better than
  // a wall of grey text. Handles JS/TS/Python/Bash/JSON/Go/Rust common
  // tokens. Order matters: comments and strings first, then keywords.
  function highlightCode(src, lang) {
    let s = escapeHtml(src);

    const COMMENT_LINE = ['python', 'py', 'bash', 'sh', 'shell', 'ruby', 'yaml'];
    const COMMENT_HASH = COMMENT_LINE.includes(lang);

    // Comments
    if (COMMENT_HASH) {
      s = s.replace(/(^|[^\\\\])(#[^\\n]*)/g, (m, p, c) => p + '<span class="com">' + c + '</span>');
    } else {
      s = s.replace(/(\\/\\/[^\\n]*)/g, '<span class="com">$1</span>');
      s = s.replace(/(\\/\\*[\\s\\S]*?\\*\\/)/g, '<span class="com">$1</span>');
    }
    // Strings (single, double, backtick) — only on lines without an existing span tag
    s = s.replace(/("(?:[^"\\\\]|\\\\.)*")/g, '<span class="str">$1</span>');
    s = s.replace(/('(?:[^'\\\\]|\\\\.)*')/g, '<span class="str">$1</span>');
    // Numbers
    s = s.replace(/\\b(\\d+\\.?\\d*)\\b/g, '<span class="num">$1</span>');
    // Keywords by language family
    const keywordSets = {
      js: 'function|const|let|var|if|else|for|while|return|class|extends|new|this|async|await|import|export|from|default|true|false|null|undefined|typeof|instanceof|try|catch|finally|throw|switch|case|break|continue',
      ts: 'function|const|let|var|if|else|for|while|return|class|extends|new|this|async|await|import|export|from|default|true|false|null|undefined|typeof|instanceof|try|catch|finally|throw|switch|case|break|continue|interface|type|enum|implements|public|private|protected|readonly',
      py: 'def|class|if|elif|else|for|while|return|import|from|as|with|try|except|finally|raise|pass|None|True|False|and|or|not|in|is|lambda|yield|async|await|self',
      sh: 'if|then|else|elif|fi|for|in|do|done|while|case|esac|function|return|echo|local|export',
      go: 'func|var|const|type|struct|interface|package|import|if|else|for|range|return|switch|case|default|break|continue|defer|go|chan|map|select|nil|true|false',
      rs: 'fn|let|mut|const|struct|enum|impl|trait|pub|use|mod|if|else|for|while|loop|match|return|break|continue|self|Self|true|false|None|Some|Ok|Err|async|await',
    };
    let kwSet = keywordSets.js;
    if (lang === 'typescript' || lang === 'ts' || lang === 'tsx') kwSet = keywordSets.ts;
    else if (lang === 'python' || lang === 'py') kwSet = keywordSets.py;
    else if (lang === 'bash' || lang === 'sh' || lang === 'shell') kwSet = keywordSets.sh;
    else if (lang === 'go' || lang === 'golang') kwSet = keywordSets.go;
    else if (lang === 'rust' || lang === 'rs') kwSet = keywordSets.rs;
    else if (lang === 'json') kwSet = 'true|false|null';
    s = s.replace(new RegExp('\\\\b(' + kwSet + ')\\\\b', 'g'), '<span class="kw">$1</span>');
    // Function call hint: word followed by (
    s = s.replace(/(\\b[a-zA-Z_][a-zA-Z0-9_]*)\\s*\\(/g, '<span class="fn">$1</span>(');
    return s;
  }

  function inline(s) {
    s = escapeHtml(s);
    // Inline code FIRST (so its contents don't get bold/italic'd)
    s = s.replace(/\`([^\`]+)\`/g, (_m, code) => '\\u0001CODE_' + btoa(unescape(encodeURIComponent(code))) + '\\u0001');
    // Bold then italic
    s = s.replace(/\\*\\*([^*]+)\\*\\*/g, '<strong>$1</strong>');
    s = s.replace(/\\*([^*]+)\\*/g, '<em>$1</em>');
    s = s.replace(/_([^_]+)_/g, '<em>$1</em>');
    // Links
    s = s.replace(/\\[([^\\]]+)\\]\\(([^)]+)\\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    // Restore inline code
    s = s.replace(/\\u0001CODE_([A-Za-z0-9+/=]+)\\u0001/g, (_m, b64) =>
      '<code class="inline">' + decodeURIComponent(escape(atob(b64))) + '</code>'
    );
    return s;
  }

  function clearLog() {
    log.innerHTML = '';
    currentTurnEl = null; currentBubbleEl = null;
    currentReasoningBody = null; currentToolRow = null;
    assistantBuffer = '';
  }

  function ensureNoEmptyState() {
    const empty = log.querySelector('.empty');
    if (empty) empty.remove();
  }

  function addUserTurn(text) {
    ensureNoEmptyState();
    const turn = document.createElement('div');
    turn.className = 'turn user';
    turn.innerHTML =
      '<div class="role"><span class="you-circle">M</span><span class="role-name">You</span></div>' +
      '<div class="bubble"></div>';
    turn.querySelector('.bubble').textContent = text;
    log.appendChild(turn);
    log.scrollTop = log.scrollHeight;
  }

  function startAssistantTurn() {
    ensureNoEmptyState();
    currentTurnEl = document.createElement('div');
    currentTurnEl.className = 'turn assistant';
    currentTurnEl.innerHTML =
      '<div class="role"><img src="' + AVATAR_URI + '" alt="" /><span class="role-name">Crowe Logic</span></div>' +
      '<div class="turn-actions"><button class="copy-btn">Copy</button></div>' +
      '<div class="bubble streaming"></div>';
    currentBubbleEl = currentTurnEl.querySelector('.bubble');
    const copyBtn = currentTurnEl.querySelector('.copy-btn');
    const turnElForCopy = currentTurnEl;
    copyBtn.addEventListener('click', () => {
      const text = turnElForCopy.querySelector('.bubble').innerText;
      navigator.clipboard.writeText(text).then(() => {
        copyBtn.textContent = 'Copied';
        setTimeout(() => { copyBtn.textContent = 'Copy'; }, 1200);
      });
    });
    assistantBuffer = '';
    log.appendChild(currentTurnEl);
    log.scrollTop = log.scrollHeight;
  }

  function scheduleAssistantRender() {
    if (assistantRenderPending) return;
    assistantRenderPending = true;
    requestAnimationFrame(() => {
      assistantRenderPending = false;
      if (currentBubbleEl) {
        currentBubbleEl.innerHTML = renderMarkdown(assistantBuffer);
        log.scrollTop = log.scrollHeight;
      }
    });
  }

  function appendToken(delta) {
    if (!currentTurnEl) startAssistantTurn();
    // First token of the answer: collapse the "Thinking..." section,
    // relabel it to "Reasoning", and mark the bubble as having text so
    // the typing-dots placeholder gets replaced by real content + the
    // streaming cursor.
    if (assistantBuffer === '' && currentTurnEl) {
      const det = currentTurnEl.querySelector('details.reasoning');
      if (det) {
        det.open = false;
        const summary = det.querySelector('summary');
        if (summary) summary.innerHTML = 'Reasoning';
      }
      if (currentBubbleEl) currentBubbleEl.classList.add('has-text');
    }
    assistantBuffer += delta;
    scheduleAssistantRender();
  }

  function appendReasoning(delta) {
    if (!currentTurnEl) startAssistantTurn();
    if (!currentReasoningBody) {
      const det = document.createElement('details');
      det.className = 'reasoning';
      // Open during streaming so the user can watch the model think,
      // then auto-collapse when the answer starts arriving.
      det.open = true;
      det.innerHTML =
        '<summary><span class="reasoning-pulse"></span>Thinking...</summary>' +
        '<div class="reasoning-body"></div>';
      currentTurnEl.insertBefore(det, currentBubbleEl);
      currentReasoningBody = det.querySelector('.reasoning-body');
    }
    currentReasoningBody.textContent += delta;
    log.scrollTop = log.scrollHeight;
  }

  function formatDuration(ms) {
    if (typeof ms !== 'number' || ms <= 0) return '';
    if (ms < 1000) return ms + 'ms';
    return (ms / 1000).toFixed(1) + 's';
  }

  function appendTool(name, status, durationMs, args, result) {
    if (!currentTurnEl) startAssistantTurn();
    if (!currentToolRow) {
      currentToolRow = document.createElement('div');
      currentToolRow.className = 'tool-row';
      const lbl = document.createElement('div');
      lbl.className = 'tool-label';
      lbl.textContent = 'Tools';
      currentToolRow.appendChild(lbl);
      // Place tool row above the bubble so users see "what the agent
      // ran" before reading the answer.
      currentTurnEl.insertBefore(currentToolRow, currentBubbleEl);
    }
    const t = document.createElement('span');
    const isError = status && status !== 'ok';
    const isRunning = !status || status === 'pending';
    t.className = 'tool ' + (isError ? 'error' : (status === 'ok' ? 'ok' : 'running'));
    const dur = formatDuration(durationMs);
    const durHtml = dur ? ' <span class="duration">' + dur + '</span>' : '';
    t.innerHTML = '<span class="tool-status"></span>' + escapeHtml(name || 'tool') + durHtml;
    // Title tooltip with args/result preview so users can hover to inspect
    const tip = [];
    if (args) tip.push('args: ' + (typeof args === 'string' ? args : JSON.stringify(args)).slice(0, 240));
    if (result) tip.push('result: ' + (typeof result === 'string' ? result : JSON.stringify(result)).slice(0, 240));
    if (tip.length) t.title = tip.join('\\n');
    currentToolRow.appendChild(t);
    log.scrollTop = log.scrollHeight;
  }

  function appendError(message, kind) {
    const e = document.createElement('div');
    e.className = 'error-banner';
    e.textContent = (kind ? '[' + kind + '] ' : '') + message;
    (currentTurnEl ?? log).appendChild(e);
    log.scrollTop = log.scrollHeight;
  }

  function appendMeta(text) {
    if (!currentTurnEl) return;
    const m = document.createElement('div');
    m.className = 'meta';
    m.textContent = text;
    currentTurnEl.appendChild(m);
  }

  function endTurn() {
    if (currentBubbleEl) currentBubbleEl.classList.remove('streaming');
    currentTurnEl = null; currentBubbleEl = null;
    currentReasoningBody = null; currentToolRow = null;
    body.classList.remove('busy');
    stopElapsed();
    promptEl.focus();
  }

  // Scroll-to-bottom button
  const scrollBtn = document.getElementById('scroll-to-bottom');
  let userScrolledUp = false;
  log.addEventListener('scroll', () => {
    const fromBottom = log.scrollHeight - log.scrollTop - log.clientHeight;
    userScrolledUp = fromBottom > 80;
    if (userScrolledUp) scrollBtn.classList.add('visible');
    else scrollBtn.classList.remove('visible');
  });
  scrollBtn.addEventListener('click', () => {
    log.scrollTop = log.scrollHeight;
    userScrolledUp = false;
    scrollBtn.classList.remove('visible');
  });

  // File-context strip
  const fileContextEl = document.getElementById('file-context');
  const fileContextName = document.getElementById('file-context-name');

  // Live elapsed time during streaming
  const elapsedEl = document.getElementById('elapsed');
  let streamStartedAt = 0;
  let elapsedTimer = null;
  function startElapsed() {
    streamStartedAt = Date.now();
    if (elapsedEl) elapsedEl.textContent = '0.0s';
    if (elapsedTimer) clearInterval(elapsedTimer);
    elapsedTimer = setInterval(() => {
      if (elapsedEl) {
        elapsedEl.textContent = ((Date.now() - streamStartedAt) / 1000).toFixed(1) + 's';
      }
    }, 100);
  }
  function stopElapsed() {
    if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null; }
  }

  composer.addEventListener('submit', (e) => {
    e.preventDefault();
    const text = promptEl.value.trim();
    if (!text || body.classList.contains('busy')) return;
    addUserTurn(text);
    promptEl.value = '';
    promptEl.style.height = 'auto';
    body.classList.add('busy');
    startElapsed();
    vscode.postMessage({ type: 'send', prompt: text });
  });
  cancelBtn.addEventListener('click', () => {
    vscode.postMessage({ type: 'cancel' });
    endTurn();
  });
  resetBtn.addEventListener('click', () => {
    vscode.postMessage({ type: 'reset' });
  });
  promptEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      composer.dispatchEvent(new Event('submit'));
    }
  });
  promptEl.addEventListener('input', () => {
    promptEl.style.height = 'auto';
    promptEl.style.height = Math.min(promptEl.scrollHeight, 240) + 'px';
  });

  window.addEventListener('message', (event) => {
    const msg = event.data;
    switch (msg.type) {
      case 'ready':       startAssistantTurn(); return;
      case 'token':       appendToken(msg.delta); return;
      case 'reasoning':   appendReasoning(msg.delta); return;
      case 'tool':        appendTool(msg.name, msg.status, msg.duration_ms, msg.args, msg.result); return;
      case 'error':       appendError(msg.message, msg.kind); endTurn(); return;
      case 'done':
        if (typeof msg.elapsed_ms === 'number') {
          appendMeta((msg.elapsed_ms / 1000).toFixed(1) + 's' + (msg.tokens ? ' · ' + msg.tokens + ' tok' : ''));
          metaStatEl.textContent = (msg.elapsed_ms / 1000).toFixed(1) + 's' + (msg.tokens ? ' · ' + msg.tokens + ' tok' : '');
        }
        endTurn();
        return;
      case 'cleared':     clearLog(); metaStatEl.textContent = ''; return;
      case 'modelChanged':
        if (msg.model) modelNameEl.textContent = msg.model;
        return;
      case 'fileContext':
        if (msg.file) {
          fileContextName.textContent = msg.file;
          fileContextEl.classList.add('visible');
        } else {
          fileContextEl.classList.remove('visible');
        }
        return;
    }
  });

  // Initial model fetch
  vscode.postMessage({ type: 'getModel' });
</script>
</body>
</html>`;
}
