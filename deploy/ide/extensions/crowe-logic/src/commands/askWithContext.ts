/**
 * Ask Crowe with editor context.
 *
 * Entry point for code-action lightbulbs and the editor context menu.
 * Wraps the user's selection (or the whole active file when nothing is
 * selected) in a fenced code block, prefills the VS Code chat with
 * `@crowe` and the appropriate slash command, and opens the chat panel.
 *
 * We deliberately don't send the prompt automatically — VS Code's chat
 * API exposes `workbench.action.chat.open` with a `query` but not an
 * "autosend" flag. Leaving the prompt staged also gives the user a
 * chance to tweak the request before firing.
 */
import * as vscode from 'vscode';

export type AskAction = 'explain' | 'refactor' | 'tests' | 'findBugs' | 'docstring';

interface AskArgs {
    action: AskAction;
    uri?: string;
    range?: { start: { line: number; character: number }; end: { line: number; character: number } };
}

const INSTRUCTIONS: Record<AskAction, string> = {
    explain: 'Explain what this code does, including any non-obvious control flow, side effects, and assumptions. Call out anything that looks wrong.',
    refactor: 'Refactor this code for readability and correctness. Preserve public behavior. Return the full replacement with a short summary of the changes you made.',
    tests: 'Write a focused unit test suite that covers the happy path plus the tricky edge cases for this code. Use the testing framework that fits the surrounding project.',
    findBugs: 'Review this code for bugs, race conditions, off-by-one errors, unhandled errors, and security issues. For each finding give a concrete fix.',
    docstring: 'Write clear, idiomatic documentation for this code (docstring / JSDoc / equivalent). Cover params, return, raises/throws, and one realistic usage note.',
};

const SLASH: Record<AskAction, string | null> = {
    explain: 'explain',
    refactor: 'refactor',
    tests: 'tests',
    findBugs: 'find-bugs',
    docstring: 'docs',
};

export async function askWithContext(args?: AskArgs): Promise<void> {
    const action: AskAction = args?.action ?? 'explain';

    const editor = vscode.window.activeTextEditor;
    const targetUri = args?.uri ? vscode.Uri.parse(args.uri) : editor?.document.uri;
    if (!editor && !targetUri) {
        await vscode.commands.executeCommand('workbench.action.chat.open', {
            query: `@crowe ${INSTRUCTIONS[action]}`,
        });
        return;
    }

    const doc = editor?.document.uri.toString() === targetUri?.toString()
        ? editor!.document
        : await vscode.workspace.openTextDocument(targetUri!);

    let range: vscode.Range | undefined;
    if (args?.range) {
        range = new vscode.Range(
            new vscode.Position(args.range.start.line, args.range.start.character),
            new vscode.Position(args.range.end.line, args.range.end.character),
        );
    } else if (editor && editor.document === doc && !editor.selection.isEmpty) {
        range = editor.selection;
    }

    const snippet = range ? doc.getText(range) : doc.getText();
    const startLine = (range?.start.line ?? 0) + 1;
    const endLine = (range?.end.line ?? doc.lineCount - 1) + 1;
    const lang = doc.languageId;
    const relPath = vscode.workspace.asRelativePath(doc.uri);

    const fence = '```';
    const prompt = [
        INSTRUCTIONS[action],
        '',
        `File: \`${relPath}\` (lines ${startLine}–${endLine})`,
        '',
        `${fence}${lang}`,
        snippet,
        fence,
    ].join('\n');

    const slash = SLASH[action];
    const query = slash ? `@crowe /${slash} ${prompt}` : `@crowe ${prompt}`;

    await vscode.commands.executeCommand('workbench.action.chat.open', { query });
}
