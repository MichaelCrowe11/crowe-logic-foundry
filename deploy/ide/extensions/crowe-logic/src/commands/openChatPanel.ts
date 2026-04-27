/**
 * `Crowe Logic: Open Chat (Panel)` — opens the Crowe Logic chat as a
 * full-size editor-area webview panel, like Claude Code's "Open in
 * Primary Editor" / "Open in New Tab" commands.
 *
 * Distinct from the sidebar webview view: this one takes a real editor
 * tab, retains scroll/state across navigations, and is the surface that
 * the title-bar/command-center icon points at.
 *
 * The panel re-uses the same HTML and message protocol as
 * CroweChatViewProvider so we don't duplicate the renderer. Only the
 * lifetime/host differs.
 */

import * as vscode from 'vscode';
import { runFoundryTurn, FoundryMessage, FoundryEvent } from '../agent';
import { resolveFoundryPath, resolvePythonPath } from '../resolvePaths';
import { renderChatHtml } from '../views/chatView';

let activePanel: vscode.WebviewPanel | undefined;
let panelHistory: FoundryMessage[] = [];

export async function openChatPanel(context: vscode.ExtensionContext): Promise<void> {
    if (activePanel) {
        activePanel.reveal(vscode.ViewColumn.Active);
        return;
    }

    const panel = vscode.window.createWebviewPanel(
        'crowe-logic.chatPanel',
        'Crowe Logic',
        vscode.ViewColumn.Active,
        {
            enableScripts: true,
            retainContextWhenHidden: true,
            localResourceRoots: [
                vscode.Uri.joinPath(context.extensionUri, 'media'),
            ],
        },
    );

    panel.iconPath = {
        light: vscode.Uri.joinPath(context.extensionUri, 'media', 'mark.svg'),
        dark: vscode.Uri.joinPath(context.extensionUri, 'media', 'mark.svg'),
    };

    panel.webview.html = renderChatHtml(panel.webview, context.extensionUri);

    let cancellation: vscode.CancellationTokenSource | undefined;

    panel.webview.onDidReceiveMessage(async (msg: { type: string; prompt?: string }) => {
        switch (msg.type) {
            case 'send':
                if (msg.prompt && msg.prompt.trim()) {
                    await handleTurn(panel, msg.prompt.trim(), cancellation);
                }
                return;
            case 'reset':
                panelHistory = [];
                panel.webview.postMessage({ type: 'cleared' });
                return;
            case 'cancel':
                cancellation?.cancel();
                return;
        }
    });

    panel.onDidDispose(() => {
        activePanel = undefined;
        cancellation?.dispose();
    });

    activePanel = panel;
}

async function handleTurn(
    panel: vscode.WebviewPanel,
    prompt: string,
    cancellation: vscode.CancellationTokenSource | undefined,
): Promise<void> {
    const cfg = vscode.workspace.getConfiguration('croweLogic');
    const model = cfg.get<string>('model') || 'auto';
    const sessionId = `crowe-panel-${Math.floor(Date.now() / 1000)}`;

    const foundryPath = resolveFoundryPath();
    const pythonPath = foundryPath ? resolvePythonPath(foundryPath) : null;

    if (!foundryPath || !pythonPath) {
        panel.webview.postMessage({
            type: 'error',
            message:
                'Foundry not found. Set `croweLogic.foundryPath` and `croweLogic.pythonPath` in settings.',
            kind: 'config',
        });
        return;
    }

    panelHistory.push({ role: 'user', content: prompt });

    cancellation?.dispose();
    const tokenSource = new vscode.CancellationTokenSource();

    try {
        const events = runFoundryTurn(
            { messages: panelHistory, model, session: sessionId },
            { pythonPath, foundryPath, cancellation: tokenSource.token },
        );

        let assistantText = '';
        for await (const evt of events as AsyncGenerator<FoundryEvent>) {
            if (tokenSource.token.isCancellationRequested) break;
            dispatchEvent(panel, evt);
            if (evt.type === 'token' && typeof evt.delta === 'string') {
                assistantText += evt.delta;
            }
            if (evt.type === 'done') break;
        }

        if (assistantText) {
            panelHistory.push({ role: 'assistant', content: assistantText });
        }
    } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        panel.webview.postMessage({ type: 'error', message: msg, kind: 'runtime' });
    } finally {
        tokenSource.dispose();
    }
}

function dispatchEvent(panel: vscode.WebviewPanel, evt: FoundryEvent): void {
    switch (evt.type) {
        case 'ready':
            panel.webview.postMessage({ type: 'ready' });
            return;
        case 'token':
            panel.webview.postMessage({ type: 'token', delta: evt.delta });
            return;
        case 'reasoning':
            panel.webview.postMessage({ type: 'reasoning', delta: evt.delta });
            return;
        case 'tool':
            panel.webview.postMessage({
                type: 'tool',
                name: evt.name,
                status: evt.status,
                duration_ms: evt.duration_ms,
                args: evt.args,
                result: evt.result,
            });
            return;
        case 'error':
            panel.webview.postMessage({
                type: 'error',
                message: evt.message ?? 'unknown failure',
                kind: evt.kind ?? 'unknown',
            });
            return;
        case 'done':
            panel.webview.postMessage({
                type: 'done',
                tokens: evt.tokens,
                elapsed_ms: evt.elapsed_ms,
            });
            return;
        default:
            return;
    }
}
