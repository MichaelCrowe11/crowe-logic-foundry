/**
 * Code-action provider.
 *
 * Surfaces Crowe Logic refactor / review actions in the editor lightbulb
 * and the right-click menu. All actions delegate to
 * `crowe-logic.askWithContext` with a stable `AskAction` kind; the command
 * handles context extraction and chat invocation.
 *
 * Actions are offered whenever there's a non-empty selection OR the file
 * has diagnostics in view (so the "review this" flow is one click from
 * an error squiggle).
 */
import * as vscode from 'vscode';
import type { AskAction } from './commands/askWithContext';

interface Definition {
    title: string;
    action: AskAction;
    kind: vscode.CodeActionKind;
    onDiagnostic?: boolean;
}

const DEFINITIONS: Definition[] = [
    { title: 'Crowe: Explain this', action: 'explain', kind: vscode.CodeActionKind.QuickFix },
    { title: 'Crowe: Refactor this', action: 'refactor', kind: vscode.CodeActionKind.RefactorRewrite },
    { title: 'Crowe: Write tests for this', action: 'tests', kind: vscode.CodeActionKind.Refactor },
    { title: 'Crowe: Find bugs in this', action: 'findBugs', kind: vscode.CodeActionKind.QuickFix, onDiagnostic: true },
    { title: 'Crowe: Write docs for this', action: 'docstring', kind: vscode.CodeActionKind.Refactor },
];

export const CROWE_CODE_ACTION_KINDS: vscode.CodeActionKind[] = [
    vscode.CodeActionKind.QuickFix,
    vscode.CodeActionKind.Refactor,
    vscode.CodeActionKind.RefactorRewrite,
];

export class CroweCodeActionProvider implements vscode.CodeActionProvider {
    provideCodeActions(
        document: vscode.TextDocument,
        range: vscode.Range | vscode.Selection,
        context: vscode.CodeActionContext,
        _token: vscode.CancellationToken,
    ): vscode.CodeAction[] {
        const hasSelection = !range.isEmpty;
        const hasDiagnostic = context.diagnostics.length > 0;
        if (!hasSelection && !hasDiagnostic) return [];

        const effectiveRange = hasSelection
            ? range
            : context.diagnostics[0].range;

        return DEFINITIONS
            .filter(def => hasSelection || def.onDiagnostic)
            .map(def => {
                const action = new vscode.CodeAction(def.title, def.kind);
                action.command = {
                    title: def.title,
                    command: 'crowe-logic.askWithContext',
                    arguments: [{
                        action: def.action,
                        uri: document.uri.toString(),
                        range: {
                            start: { line: effectiveRange.start.line, character: effectiveRange.start.character },
                            end: { line: effectiveRange.end.line, character: effectiveRange.end.character },
                        },
                    }],
                };
                action.isPreferred = def.action === 'findBugs' && hasDiagnostic;
                return action;
            });
    }
}
