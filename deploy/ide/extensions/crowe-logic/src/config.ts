/**
 * Configuration + secret storage helpers.
 *
 * PATs are stored in VS Code SecretStorage under `croweLogic.apiToken`
 * so they survive restarts but are encrypted by the OS keychain.
 */
import * as vscode from 'vscode';

export const TOKEN_SECRET_KEY = 'croweLogic.apiToken';

export function getApiBaseUrl(): string {
    const cfg = vscode.workspace.getConfiguration('croweLogic');
    return (cfg.get<string>('apiBaseUrl') ?? 'https://api.crowelogic.com').replace(/\/+$/, '');
}

export function getIdeUrl(): string {
    const cfg = vscode.workspace.getConfiguration('croweLogic');
    return (cfg.get<string>('ideUrl') ?? 'https://ide.crowelogic.com').replace(/\/+$/, '');
}

export async function getApiToken(ctx: vscode.ExtensionContext): Promise<string | undefined> {
    return ctx.secrets.get(TOKEN_SECRET_KEY);
}

export async function setApiToken(ctx: vscode.ExtensionContext, token: string): Promise<void> {
    await ctx.secrets.store(TOKEN_SECRET_KEY, token);
}

export async function clearApiToken(ctx: vscode.ExtensionContext): Promise<void> {
    await ctx.secrets.delete(TOKEN_SECRET_KEY);
}

export async function authHeaders(ctx: vscode.ExtensionContext): Promise<Record<string, string>> {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    const token = await getApiToken(ctx);
    if (token) headers['Authorization'] = `Bearer ${token}`;
    return headers;
}
