/**
 * Open in Remote IDE.
 *
 * Posts to the Crowe Logic AI platform's /api/ide/launch endpoint with
 * the stored PAT, then opens the returned single-use handoff URL in the
 * user's browser. The Session Router on the IDE host verifies the
 * short-lived JWT, sets a session cookie, and redirects the user into
 * their code-server container.
 *
 * If no token is configured, falls back to opening the IDE origin so the
 * user lands on the sign-in page.
 */
import * as vscode from 'vscode';
import { authHeaders, getApiBaseUrl, getIdeUrl } from '../config';

export async function openInRemoteIde(ctx: vscode.ExtensionContext): Promise<void> {
    const apiBaseUrl = getApiBaseUrl();
    const ideUrl = getIdeUrl();

    await vscode.window.withProgress(
        {
            location: vscode.ProgressLocation.Notification,
            title: 'Crowe Logic: requesting IDE handoff…',
        },
        async () => {
            try {
                const headers = await authHeaders(ctx);
                const res = await fetch(`${apiBaseUrl}/api/ide/launch`, {
                    method: 'POST',
                    headers,
                });

                if (res.status === 401) {
                    const pick = await vscode.window.showWarningMessage(
                        'Not authenticated. Sign in with your Crowe Logic API token?',
                        'Sign In',
                        'Open Sign-in Page',
                    );
                    if (pick === 'Sign In') {
                        await vscode.commands.executeCommand('crowe-logic.signIn');
                    } else if (pick === 'Open Sign-in Page') {
                        await vscode.env.openExternal(vscode.Uri.parse(`${apiBaseUrl}/auth`));
                    }
                    return;
                }

                if (!res.ok) {
                    const body = await res.text().catch(() => '');
                    throw new Error(`${res.status} ${res.statusText}: ${body.slice(0, 200)}`);
                }

                const json = (await res.json()) as { url?: string; error?: string };
                const url = json.url;
                if (!url) {
                    throw new Error(json.error ?? 'No URL returned from /api/ide/launch');
                }
                await vscode.env.openExternal(vscode.Uri.parse(url));
            } catch (err) {
                const msg = err instanceof Error ? err.message : String(err);
                const pick = await vscode.window.showErrorMessage(
                    `Failed to open remote IDE: ${msg}`,
                    'Open IDE Home',
                );
                if (pick === 'Open IDE Home') {
                    await vscode.env.openExternal(vscode.Uri.parse(ideUrl));
                }
            }
        },
    );
}
