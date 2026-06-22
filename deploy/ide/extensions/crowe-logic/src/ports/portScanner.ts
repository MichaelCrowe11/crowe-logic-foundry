/**
 * PortScanner — polls the host for LISTEN TCP ports at a configurable
 * interval, diffs against the previous scan, and fires an event when
 * ports are added or removed. Used by BrowserViewProvider to populate
 * the port-chip row and optionally auto-preview newly opened services.
 *
 * Scan strategy:
 *   1. `ss -ltn` (requires iproute2, installed via Dockerfile)
 *   2. Fallback: /proc/net/tcp + /proc/net/tcp6 (state 0A = LISTEN)
 */

import * as vscode from 'vscode';
import * as child_process from 'child_process';
import * as fs from 'fs';

export interface PortChangeEvent {
    ports: number[];
    added: number[];
    removed: number[];
}

export class PortScanner implements vscode.Disposable {
    private readonly _onDidChange = new vscode.EventEmitter<PortChangeEvent>();
    public readonly onDidChange: vscode.Event<PortChangeEvent> = this._onDidChange.event;

    private _timer: NodeJS.Timeout | undefined;
    private _previous: Set<number> = new Set();
    private _started = false;

    constructor(private readonly opts: { intervalMs: number; ignore: Set<number> }) {}

    start(): void {
        if (this._started) return;
        this._started = true;
        this._tick(true);
    }

    refresh(): void {
        this._tick(false);
    }

    dispose(): void {
        if (this._timer) {
            clearTimeout(this._timer);
            this._timer = undefined;
        }
        this._onDidChange.dispose();
    }

    private _tick(seed: boolean): void {
        if (this._timer) {
            clearTimeout(this._timer);
            this._timer = undefined;
        }

        const ports = this._scan();
        const current = new Set(ports);

        if (seed) {
            // First scan: seed previous, never emit added (pre-existing ports
            // should not trigger auto-open).
            this._previous = current;
            this._onDidChange.fire({ ports, added: [], removed: [] });
        } else {
            const added: number[] = [];
            const removed: number[] = [];
            for (const p of current) {
                if (!this._previous.has(p)) added.push(p);
            }
            for (const p of this._previous) {
                if (!current.has(p)) removed.push(p);
            }
            this._previous = current;
            if (added.length > 0 || removed.length > 0) {
                this._onDidChange.fire({ ports, added, removed });
            }
        }

        this._timer = setTimeout(() => this._tick(false), this.opts.intervalMs);
    }

    private _scan(): number[] {
        const ports = new Set<number>();

        // Strategy 1: ss -ltn
        try {
            const out = child_process.execFileSync('ss', ['-ltn'], { encoding: 'utf8', timeout: 2000 });
            for (const line of out.split('\n')) {
                // Lines look like: LISTEN 0 128 0.0.0.0:3000 0.0.0.0:*
                const m = /\s(\d+\.\d+\.\d+\.\d+|::|\*|0\.0\.0\.0|\[::\]):(\d+)\s/.exec(line);
                if (m) {
                    const port = parseInt(m[2], 10);
                    if (!isNaN(port) && !this.opts.ignore.has(port)) {
                        ports.add(port);
                    }
                }
            }
            return [...ports].sort((a, b) => a - b);
        } catch {
            // ss not available or failed — fall through to /proc
        }

        // Strategy 2: /proc/net/tcp and /proc/net/tcp6
        for (const file of ['/proc/net/tcp', '/proc/net/tcp6']) {
            try {
                const content = fs.readFileSync(file, 'utf8');
                for (const line of content.split('\n').slice(1)) {
                    const cols = line.trim().split(/\s+/);
                    if (cols.length < 4) continue;
                    // State is column index 3; 0A = LISTEN
                    if (cols[3] !== '0A') continue;
                    // Local address is col 1: hex_addr:hex_port
                    const addrPart = cols[1];
                    const colonIdx = addrPart.lastIndexOf(':');
                    if (colonIdx < 0) continue;
                    const hexPort = addrPart.slice(colonIdx + 1);
                    const port = parseInt(hexPort, 16);
                    if (!isNaN(port) && port > 0 && !this.opts.ignore.has(port)) {
                        ports.add(port);
                    }
                }
            } catch {
                // /proc not available (non-Linux)
            }
        }

        return [...ports].sort((a, b) => a - b);
    }
}
