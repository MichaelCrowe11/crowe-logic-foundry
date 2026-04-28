/**
 * Status-bar item for Crowe Logic.
 *
 * Renders a single status-bar entry on the right that reflects the
 * stored PAT state and offers a quick-pick menu of the most common
 * actions (open chat, open remote IDE, sign in / out). The text
 * updates whenever the secret changes so signing in/out anywhere is
 * reflected immediately.
 */
import * as vscode from 'vscode';
import { TOKEN_SECRET_KEY, getApiToken } from './config';

const QUICK_PICK_COMMAND = 'crowe-logic.statusBarMenu';

export async function registerStatusBar(ctx: vscode.ExtensionContext): Promise<void> {
    const item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    item.command = QUICK_PICK_COMMAND;
    item.tooltip = 'Crowe Logic';

    const refresh = async () => {
        const token = await getApiToken(ctx);
        const cfg = vscode.workspace.getConfiguration('croweLogic');
        const model = cfg.get<string>('model') || 'auto';
        const modelLabel = formatModelLabel(model);
        const bridgeIcon = await probeBridgeIcon();
        if (token) {
            item.text = `$(sparkle) Crowe Logic · ${modelLabel}${bridgeIcon}`;
            item.tooltip = `Crowe Logic Workstation\nModel: CroweLM ${modelLabel}\nSigned in. Click for actions.`;
        } else {
            item.text = `$(account) Crowe Logic · sign in${bridgeIcon}`;
            item.tooltip = 'Crowe Logic Workstation — not signed in. Click to sign in.';
        }
        item.show();
    };

    const refreshInterval = setInterval(() => {
        void refresh();
    }, 30_000);
    ctx.subscriptions.push({ dispose: () => clearInterval(refreshInterval) });

    // Re-render when the user changes the active model from the chat picker
    // or settings UI so the status bar always reflects what they'll talk to.
    ctx.subscriptions.push(
        vscode.workspace.onDidChangeConfiguration((e) => {
            if (e.affectsConfiguration('croweLogic.model')) void refresh();
        }),
    );

    ctx.subscriptions.push(
        item,
        ctx.secrets.onDidChange((e) => {
            if (e.key === TOKEN_SECRET_KEY) void refresh();
        }),
        vscode.commands.registerCommand(QUICK_PICK_COMMAND, async () => {
            const signedIn = !!(await getApiToken(ctx));
            const cfg = vscode.workspace.getConfiguration('croweLogic');
            const currentModel = cfg.get<string>('model') || 'auto';
            const items: (vscode.QuickPickItem & { id: string })[] = [
                { id: 'chat', label: '$(comment-discussion) Open Crowe Logic chat', description: 'Sidebar' },
                { id: 'panel', label: '$(window) Open chat in new tab', description: 'Editor area' },
                { id: 'cli', label: '$(terminal) Start Crowe Logic CLI', description: 'Terminal' },
                { id: 'pickModel', label: '$(symbol-event) Pick model', description: formatModelLabel(currentModel) },
                { id: 'remote', label: '$(remote) Open in remote IDE', description: 'ide.crowelogic.com' },
                signedIn
                    ? { id: 'signOut', label: '$(sign-out) Sign out' }
                    : { id: 'signIn', label: '$(sign-in) Sign in with API token' },
            ];
            const pick = await vscode.window.showQuickPick(items, {
                title: 'Crowe Logic Workstation',
                placeHolder: signedIn ? `Signed in · ${formatModelLabel(currentModel)}` : 'Not signed in',
            });
            if (!pick) return;
            switch (pick.id) {
                case 'chat':
                    await vscode.commands.executeCommand('crowe-logic.openChat');
                    return;
                case 'panel':
                    await vscode.commands.executeCommand('crowe-logic.openChatPanel');
                    return;
                case 'cli':
                    await vscode.commands.executeCommand('crowe-logic.openCli');
                    return;
                case 'pickModel':
                    await vscode.commands.executeCommand('crowe-logic.pickModel');
                    return;
                case 'remote':
                    await vscode.commands.executeCommand('crowe-logic.openInRemoteIde');
                    return;
                case 'signIn':
                    await vscode.commands.executeCommand('crowe-logic.signIn');
                    return;
                case 'signOut':
                    await vscode.commands.executeCommand('crowe-logic.signOut');
                    return;
            }
        }),
    );

    await refresh();
}

/**
 * Probe the local foundry bridge once per status-bar refresh.
 *
 * Returns a small icon to append to the status text:
 *   - empty string when bridge is healthy
 *   - "$(warning)" when bridge isn't reachable on 127.0.0.1:8011
 * Times out fast (1s) to avoid blocking the status bar.
 */
async function probeBridgeIcon(): Promise<string> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 1000);
    try {
        const res = await fetch('http://127.0.0.1:8011/healthz', { signal: controller.signal });
        if (res.ok) return '';
    } catch {
        // bridge unreachable; fall through
    } finally {
        clearTimeout(timeout);
    }
    return ' $(warning)';
}

/**
 * Map an internal model id to a short Crowe Logic tier label.
 *
 * Only CroweLM tier names are surfaced; upstream provider/model brand
 * names are deliberately not exposed in user-visible UI.
 */
function formatModelLabel(model: string): string {
    if (!model || model === 'auto' || model.toLowerCase().includes('auto')) {
        return 'Auto';
    }
    const m = model.toLowerCase();
    if (m.includes('supreme')) return 'Supreme';
    if (m.includes('apex')) return 'Apex';
    if (m.includes('titan')) return 'Titan';
    if (m.includes('oracle')) return 'Oracle';
    if (m.includes('sovereign')) return 'Sovereign';
    if (m.includes('talon')) return 'Talon';
    if (m.includes('classic')) return 'Classic';
    // Anything else is mapped to "CroweLM" so we never leak a backend name.
    return 'CroweLM';
}
