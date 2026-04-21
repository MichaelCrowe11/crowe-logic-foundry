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
        if (token) {
            item.text = '$(sparkle) Crowe';
            item.tooltip = 'Crowe Logic — signed in. Click for actions.';
        } else {
            item.text = '$(account) Crowe: sign in';
            item.tooltip = 'Crowe Logic — not signed in. Click to sign in.';
        }
        item.show();
    };

    ctx.subscriptions.push(
        item,
        ctx.secrets.onDidChange((e) => {
            if (e.key === TOKEN_SECRET_KEY) void refresh();
        }),
        vscode.commands.registerCommand(QUICK_PICK_COMMAND, async () => {
            const signedIn = !!(await getApiToken(ctx));
            const items: (vscode.QuickPickItem & { id: string })[] = [
                { id: 'chat', label: '$(comment-discussion) Open Crowe chat', description: '@crowe' },
                { id: 'remote', label: '$(remote) Open in remote IDE', description: 'Hand off to ai.southwestmushrooms.com' },
                signedIn
                    ? { id: 'signOut', label: '$(sign-out) Sign out' }
                    : { id: 'signIn', label: '$(sign-in) Sign in with API token' },
            ];
            const pick = await vscode.window.showQuickPick(items, {
                title: 'Crowe Logic',
                placeHolder: signedIn ? 'Signed in' : 'Not signed in',
            });
            if (!pick) return;
            switch (pick.id) {
                case 'chat':
                    await vscode.commands.executeCommand('crowe-logic.openChat');
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
