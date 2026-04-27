/**
 * `Crowe Logic: Open CLI` — opens an integrated terminal running the
 * `crowe-logic` command. This is the in-IDE equivalent of GitHub
 * Copilot's CLI integration: a first-class terminal companion that
 * shows the welcome banner, listens for prompts, and routes through
 * the same model chain as the @crowe chat participant.
 *
 * Resolution rules (first match wins):
 *   1. The Foundry venv we already trust: `<foundryPath>/.venv/bin/crowe-logic`
 *   2. The user-installed CLI on PATH: `crowe-logic`
 *   3. Fallback: `python -m cli.crowe_logic` from the foundry repo
 *
 * If neither path resolves, we open the terminal anyway with a
 * helpful message so the user can fix it without losing the terminal.
 */

import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { resolveFoundryPath, resolvePythonPath } from '../resolvePaths';

export const TERMINAL_NAME = 'Crowe Logic CLI';

function findCroweLogicBinary(foundryPath: string | null): { cmd: string; cwd: string } | null {
    // Strategy 1: Foundry venv binary (highest confidence; this is the
    // path the user has already configured for Python.)
    if (foundryPath) {
        const venvBin = path.join(foundryPath, '.venv', 'bin', 'crowe-logic');
        if (fs.existsSync(venvBin)) {
            return { cmd: venvBin, cwd: foundryPath };
        }
    }
    // Strategy 2: PATH lookup. We don't shell out here; we trust that
    // the integrated terminal will resolve `crowe-logic` against PATH.
    // If it doesn't exist, the terminal prints "command not found",
    // which is a clearer error than us guessing wrong.
    return null;
}

export async function openCli(context: vscode.ExtensionContext): Promise<void> {
    // If a Crowe Logic terminal is already open, focus it instead of
    // spawning a duplicate.
    const existing = vscode.window.terminals.find((t) => t.name === TERMINAL_NAME);
    if (existing) {
        existing.show(false);
        return;
    }

    const foundryPath = resolveFoundryPath();
    const located = findCroweLogicBinary(foundryPath);

    let cmd: string;
    let cwd: string | undefined;

    if (located) {
        cmd = located.cmd;
        cwd = located.cwd;
    } else if (foundryPath) {
        // Fall back to running the CLI module directly via the configured
        // Python interpreter. This works even when the user hasn't run
        // `pip install -e .` in their venv yet.
        const py = resolvePythonPath(foundryPath);
        if (py) {
            cmd = `${py} -m cli.crowe_logic`;
            cwd = foundryPath;
        } else {
            cmd = 'crowe-logic';
        }
    } else {
        // Neither foundry path nor venv resolved — open a terminal with
        // a hint message so the user understands what's missing.
        cmd = 'crowe-logic';
    }

    const term = vscode.window.createTerminal({
        name: TERMINAL_NAME,
        cwd,
        iconPath: vscode.Uri.file(
            path.join(context.extensionPath, 'media', 'mark.svg'),
        ),
        // Pass an env var so the CLI can identify it's running under VS Code
        // and tune output (e.g., enable inline iTerm-style images if iTerm
        // protocol is available, otherwise fall back to glyph mark).
        env: {
            CROWE_LOGIC_LAUNCHED_BY: 'vscode-extension',
            CROWE_LOGIC_PROJECT_ROOT: foundryPath ?? '',
        },
    });

    term.show(false);
    term.sendText(cmd, true);
}
