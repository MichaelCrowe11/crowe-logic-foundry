/**
 * Sign-in / sign-out commands. The extension stores a Crowe Logic PAT
 * (`crowe_pat_...`) in SecretStorage; it's used for programmatic access
 * to the AI platform (chat history, IDE launch, etc.) independent of
 * the browser session.
 */
import * as vscode from 'vscode';
import { clearApiToken, setApiToken } from '../config';

export async function signIn(ctx: vscode.ExtensionContext): Promise<void> {
    const token = await vscode.window.showInputBox({
        prompt: 'Paste your Crowe Logic API token',
        placeHolder: 'crowe_pat_...',
        password: true,
        ignoreFocusOut: true,
        validateInput: (v) =>
            v && v.trim().startsWith('crowe_pat_')
                ? null
                : 'Token should start with "crowe_pat_"',
    });
    if (!token) return;
    await setApiToken(ctx, token.trim());
    vscode.window.showInformationMessage('Crowe Logic: signed in.');
}

export async function signOut(ctx: vscode.ExtensionContext): Promise<void> {
    await clearApiToken(ctx);
    vscode.window.showInformationMessage('Crowe Logic: signed out.');
}
