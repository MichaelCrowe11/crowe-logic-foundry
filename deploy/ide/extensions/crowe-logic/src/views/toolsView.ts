/**
 * Tool Activity tree view.
 *
 * Receives tool events from the chat handler (one node per tool call,
 * status icon for ok/fail, duration in the description). Mirrors the
 * tool cards the terminal CLI renders inline, but in a persistent
 * sidebar that survives chat scroll.
 */

import * as vscode from 'vscode';

export interface ToolEntry {
    name: string;
    args: string;
    status: 'ok' | 'fail' | 'running';
    durationMs?: number;
    result?: string;
    timestamp: number;
}

class ToolNode extends vscode.TreeItem {
    constructor(public readonly entry: ToolEntry) {
        super(entry.name, vscode.TreeItemCollapsibleState.None);
        this.description = ToolNode.describe(entry);
        this.tooltip = ToolNode.tooltip(entry);
        this.iconPath = ToolNode.icon(entry);
        this.contextValue = `crowe-logic.tool.${entry.status}`;
    }

    private static describe(e: ToolEntry): string {
        if (e.status === 'running') return 'running…';
        const ms = e.durationMs ?? 0;
        return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
    }

    static readonly MAX_ENTRIES = 200;

    private static tooltip(e: ToolEntry): vscode.MarkdownString {
        const md = new vscode.MarkdownString();
        md.appendMarkdown(`**${e.name}**\n\n`);
        if (e.args) {
            md.appendCodeblock(e.args, 'json');
        }
        if (e.result) {
            md.appendMarkdown('\n**result**\n');
            md.appendCodeblock(e.result.slice(0, 2000), 'text');
        }
        md.isTrusted = false;
        return md;
    }

    private static icon(e: ToolEntry): vscode.ThemeIcon {
        switch (e.status) {
            case 'ok': return new vscode.ThemeIcon('check', new vscode.ThemeColor('charts.green'));
            case 'fail': return new vscode.ThemeIcon('error', new vscode.ThemeColor('charts.red'));
            case 'running': return new vscode.ThemeIcon('loading~spin');
        }
    }
}

export class ToolsViewProvider implements vscode.TreeDataProvider<ToolNode> {
    // Newest entries are at index 0 — push() unshifts so getChildren()
    // doesn't have to sort or reverse on every refresh.
    private entries: ToolEntry[] = [];
    private _onDidChange = new vscode.EventEmitter<ToolNode | undefined | void>();
    readonly onDidChangeTreeData = this._onDidChange.event;

    getTreeItem(element: ToolNode): vscode.TreeItem { return element; }

    getChildren(): ToolNode[] {
        return this.entries.map(e => new ToolNode(e));
    }

    push(entry: ToolEntry): void {
        this.entries.unshift(entry);
        // Cap to keep long sessions from leaking — older tool runs
        // scroll out of view in the chat anyway, so the activity-bar
        // pane only needs the recent window.
        if (this.entries.length > ToolNode.MAX_ENTRIES) {
            this.entries.length = ToolNode.MAX_ENTRIES;
        }
        this._onDidChange.fire();
    }

    clear(): void {
        this.entries = [];
        this._onDidChange.fire();
    }
}
