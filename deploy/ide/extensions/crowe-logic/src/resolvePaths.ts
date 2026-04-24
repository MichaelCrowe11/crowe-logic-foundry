/**
 * Path resolution for the Foundry headless runner.
 *
 * The extension ships inside a container where Python lives at
 * /opt/venv/bin/python3 and the repo at /workspace/crowe-logic-foundry,
 * but it is ALSO installed into local VS Code via `code --install-extension`.
 * In the local case those paths do not exist and spawning them produces
 * ENOENT before any useful error reaches the user. This resolver picks
 * the first existing candidate, caches it for the session, and surfaces
 * a clear message if everything fails.
 */

import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { spawnSync } from 'child_process';
import * as vscode from 'vscode';

let cachedPython: string | null = null;
let cachedFoundry: string | null = null;

export function clearPathCache(): void {
    cachedPython = null;
    cachedFoundry = null;
}

function fileIsExecutable(p: string): boolean {
    try {
        fs.accessSync(p, fs.constants.X_OK);
        return fs.statSync(p).isFile();
    } catch {
        return false;
    }
}

function directoryLooksLikeFoundry(dir: string): boolean {
    try {
        return fs.statSync(path.join(dir, 'cli', 'headless.py')).isFile();
    } catch {
        return false;
    }
}

/**
 * Resolve the Foundry checkout. Priority:
 *  1. Explicit `croweLogic.foundryPath` setting (honored even if it points nowhere; user wins).
 *  2. The first VS Code workspace folder whose root contains cli/headless.py.
 *  3. Common home-dir locations (matches CLAUDE.md layout).
 *  4. The container default /workspace/crowe-logic-foundry.
 */
export function resolveFoundryPath(): string {
    if (cachedFoundry !== null) return cachedFoundry;

    const cfg = vscode.workspace.getConfiguration('croweLogic').get<string>('foundryPath', '').trim();
    if (cfg) {
        cachedFoundry = cfg;
        return cfg;
    }

    for (const folder of vscode.workspace.workspaceFolders ?? []) {
        const fsPath = folder.uri.fsPath;
        if (directoryLooksLikeFoundry(fsPath)) {
            cachedFoundry = fsPath;
            return fsPath;
        }
    }

    const home = os.homedir();
    const homeCandidates = [
        path.join(home, 'Projects', 'crowe-logic-foundry'),
        path.join(home, 'crowe-logic-foundry'),
    ];
    for (const c of homeCandidates) {
        if (directoryLooksLikeFoundry(c)) {
            cachedFoundry = c;
            return c;
        }
    }

    cachedFoundry = '/workspace/crowe-logic-foundry';
    return cachedFoundry;
}

/**
 * Resolve the Python interpreter. Priority:
 *  1. Explicit `croweLogic.pythonPath` setting.
 *  2. A venv inside the foundry checkout (.venv or venv).
 *  3. The container default /opt/venv/bin/python3.
 *  4. python3 on PATH (via `which` / `where`).
 *  5. python on PATH (legacy).
 *
 * Returns null if no candidate is executable; the caller should show a
 * helpful error pointing at the setting rather than spawning blind.
 */
export function resolvePythonPath(foundryPath: string): string | null {
    if (cachedPython !== null) return cachedPython;

    const cfg = vscode.workspace.getConfiguration('croweLogic').get<string>('pythonPath', '').trim();
    if (cfg) {
        // Trust user override even if not executable; spawn error will be clearer.
        cachedPython = cfg;
        return cfg;
    }

    const isWindows = process.platform === 'win32';
    const bin = isWindows ? 'Scripts' : 'bin';
    const exe = isWindows ? 'python.exe' : 'python3';

    const candidates = [
        path.join(foundryPath, '.venv', bin, exe),
        path.join(foundryPath, '.venv', bin, isWindows ? 'python.exe' : 'python'),
        path.join(foundryPath, 'venv', bin, exe),
        '/opt/venv/bin/python3',
    ];
    for (const c of candidates) {
        if (fileIsExecutable(c)) {
            cachedPython = c;
            return c;
        }
    }

    const finder = isWindows ? 'where' : 'which';
    for (const name of ['python3', 'python']) {
        const r = spawnSync(finder, [name], { encoding: 'utf8' });
        if (r.status === 0) {
            const firstLine = r.stdout.split(/\r?\n/).find(Boolean);
            if (firstLine && fileIsExecutable(firstLine)) {
                cachedPython = firstLine;
                return firstLine;
            }
        }
    }

    return null;
}

/**
 * Compose a user-facing error when Python resolution fails. Includes the
 * checked candidates so support can diagnose a wrong venv without asking
 * the user to re-run with verbose logging.
 */
export function pythonNotFoundMessage(foundryPath: string): string {
    return [
        'Crowe Logic: no Python interpreter found.',
        `Checked: ${foundryPath}/.venv/bin/python3, ${foundryPath}/venv/bin/python3, /opt/venv/bin/python3, and PATH.`,
        'Set "croweLogic.pythonPath" in VS Code settings to the Python that has the Foundry deps installed.',
    ].join('\n');
}
