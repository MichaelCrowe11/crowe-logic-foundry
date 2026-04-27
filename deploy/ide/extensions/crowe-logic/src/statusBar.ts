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
        if (token) {
            // CLI parity: "MODEL · session" reads like the boxed CLI session header.
            item.text = `$(sparkle) Crowe Logic · ${modelLabel}`;
            item.tooltip = `Crowe Logic Workstation\nModel: ${modelLabel}\nSigned in. Click for actions.`;
        } else {
            item.text = '$(account) Crowe Logic · sign in';
            item.tooltip = 'Crowe Logic Workstation — not signed in. Click to sign in.';
        }
        item.show();
    };

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
 * Map an internal model id ("CroweLM Talon", "gpt-5.4-pro-managed", "auto")
 * to a short display label suitable for a status bar entry.
 */
function formatModelLabel(model: string): string {
    if (!model || model === 'auto') return 'auto';
    // Drop "CroweLM " prefix; "CroweLM Talon" → "Talon".
    if (model.startsWith('CroweLM ')) return model.slice('CroweLM '.length);
    // Map common backend names to short forms.
    if (model.includes('gpt-5.4-pro')) return 'gpt-5.4 pro';
    if (model.includes('gpt-5.4')) return 'gpt-5.4';
    if (model.includes('gpt-4o')) return 'gpt-4o';
    if (model.includes('claude')) return 'claude';
    if (model.includes('kimi')) return 'kimi';
    if (model.includes('deepseek')) return 'deepseek';
    return model;
}
