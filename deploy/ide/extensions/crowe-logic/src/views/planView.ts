/**
 * Plan tree view.
 *
 * Holds a lightweight, in-memory list of plan steps for the current
 * turn. The chat handler updates the plan as the agent narrates;
 * future iterations can parse structured plan blocks from the model's
 * output, but the data shape is already set up for that.
 */

import * as vscode from 'vscode';

export type PlanStatus = 'pending' | 'running' | 'done' | 'failed';

export interface PlanStep {
    id: string;
    title: string;
    status: PlanStatus;
    detail?: string;
}

class PlanNode extends vscode.TreeItem {
    constructor(public readonly step: PlanStep) {
        super(step.title, vscode.TreeItemCollapsibleState.None);
        this.description = step.detail;
        this.iconPath = PlanNode.icon(step.status);
        this.contextValue = `crowe-logic.plan.${step.status}`;
    }

    private static icon(status: PlanStatus): vscode.ThemeIcon {
        switch (status) {
            case 'pending': return new vscode.ThemeIcon('circle-large-outline');
            case 'running': return new vscode.ThemeIcon('loading~spin');
            case 'done': return new vscode.ThemeIcon('pass-filled', new vscode.ThemeColor('charts.green'));
            case 'failed': return new vscode.ThemeIcon('error', new vscode.ThemeColor('charts.red'));
        }
    }
}

export class PlanViewProvider implements vscode.TreeDataProvider<PlanNode> {
    private steps: PlanStep[] = [];
    private _onDidChange = new vscode.EventEmitter<PlanNode | undefined | void>();
    readonly onDidChangeTreeData = this._onDidChange.event;

    getTreeItem(element: PlanNode): vscode.TreeItem { return element; }

    getChildren(): PlanNode[] {
        return this.steps.map(s => new PlanNode(s));
    }

    setSteps(steps: PlanStep[]): void {
        this.steps = steps;
        this._onDidChange.fire();
    }

    updateStatus(id: string, status: PlanStatus, detail?: string): void {
        const step = this.steps.find(s => s.id === id);
        if (!step) return;
        step.status = status;
        if (detail !== undefined) step.detail = detail;
        this._onDidChange.fire();
    }

    clear(): void {
        this.steps = [];
        this._onDidChange.fire();
    }
}
