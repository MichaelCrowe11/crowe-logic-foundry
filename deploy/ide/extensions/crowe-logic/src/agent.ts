/**
 * Bridge to the Crowe Logic Foundry headless runner.
 *
 * Spawns `python -m cli.headless`, writes one JSON object on stdin
 * (the full conversation history for this turn), and yields parsed
 * line-delimited JSON events from stdout. The protocol is owned by
 * cli/headless.py — this file is just the host-side parser.
 *
 * Event shapes (mirrors cli/headless.py emit() calls):
 *   {type: "ready"}
 *   {type: "reasoning", delta: string}
 *   {type: "token", delta: string}
 *   {type: "tool", name, args, status: "ok"|"fail", result, duration_ms}
 *   {type: "spinner", label: string|null}
 *   {type: "segment_end"}
 *   {type: "done", tokens, reasoning_tokens, elapsed_ms, ttft_ms}
 *   {type: "error", message, kind}
 */

import { spawn } from 'child_process';
import { StringDecoder } from 'string_decoder';

export interface FoundryMessage {
    role: 'user' | 'assistant';
    content: string;
}

export interface AgentRequest {
    messages: FoundryMessage[];
    model?: string;
    session?: string;
}

export type FoundryEvent =
    | { type: 'ready' }
    | { type: 'reasoning'; delta: string }
    | { type: 'token'; delta: string }
    | { type: 'tool'; name: string; args: string; status: 'ok' | 'fail'; result: string; duration_ms: number }
    | { type: 'spinner'; label: string | null }
    | { type: 'segment_end' }
    | { type: 'done'; tokens: number; reasoning_tokens: number; elapsed_ms: number; ttft_ms: number }
    | { type: 'error'; message: string; kind: string };

export interface AgentOptions {
    pythonPath: string;
    foundryPath: string;
    /**
     * Working directory for the spawned headless process. Should be the
     * user's currently-open workspace, NOT the Foundry checkout, so the
     * agent's filesystem/shell/git tools operate on the user's project.
     * Falls back to foundryPath only when no workspace is open (e.g. the
     * user is using the chat from the welcome view with nothing loaded).
     * PYTHONPATH stays pinned to foundryPath so cli.headless can be
     * imported regardless of cwd.
     */
    workspaceFolder?: string;
    cancellation?: { isCancellationRequested: boolean; onCancellationRequested: (cb: () => void) => void };
}

/**
 * Run a single Foundry turn and yield events as they arrive.
 *
 * The async generator pattern matches VS Code's chat handler contract:
 * the handler awaits each event and dispatches it to ChatResponseStream.
 * Buffering happens here (line splitter) so the consumer never sees a
 * partial event.
 */
export async function* runFoundryTurn(
    request: AgentRequest,
    options: AgentOptions
): AsyncGenerator<FoundryEvent, void, void> {
    const cwd = options.workspaceFolder || options.foundryPath;
    const child = spawn(
        options.pythonPath,
        ['-m', 'cli.headless'],
        {
            cwd,
            env: {
                ...process.env,
                PYTHONPATH: options.foundryPath,
                // Surface foundry path to headless tools that need to find
                // training data, agent profiles, etc. without binding cwd.
                CROWE_FOUNDRY_PATH: options.foundryPath,
                CROWE_WORKSPACE_PATH: cwd,
            },
            stdio: ['pipe', 'pipe', 'pipe'],
        }
    );

    if (options.cancellation) {
        options.cancellation.onCancellationRequested(() => {
            try { child.kill('SIGTERM'); } catch { /* already dead */ }
        });
    }

    // Write the request payload and close stdin so headless.py's
    // sys.stdin.read() returns. Without this close, the Python side
    // blocks forever waiting for EOF.
    child.stdin.write(JSON.stringify(request));
    child.stdin.end();

    // Capture stderr for error reporting; the headless side should
    // emit structured errors via stdout, but a Python crash before
    // emit() runs (import errors, etc.) lands here.
    const stderrDecoder = new StringDecoder('utf8');
    let stderrBuffer = '';
    child.stderr.on('data', (chunk: Buffer) => { stderrBuffer += stderrDecoder.write(chunk); });

    // Line splitter: stdout is NDJSON, but a single chunk may contain
    // a partial line at the end (and may also split a multi-byte UTF-8
    // sequence across chunks — that's why StringDecoder, not toString).
    // `pending` holds the tail of the current chunk until \n arrives.
    const stdoutDecoder = new StringDecoder('utf8');
    let pending = '';
    const queue: FoundryEvent[] = [];
    let resolveNext: (() => void) | null = null;
    let closed = false;
    let exitError: Error | null = null;
    // Backpressure threshold: when the queue gets above HIGH_WATER and
    // the consumer hasn't drained it, pause stdout. Resume when it
    // drops below LOW_WATER. Prevents unbounded growth if a slow chat
    // webview can't keep up with a fast model.
    const HIGH_WATER = 500;
    const LOW_WATER = 100;

    const wake = () => {
        const r = resolveNext;
        resolveNext = null;
        if (r) r();
    };

    const parseLines = (input: string): void => {
        // Cursor-based scan: we never re-walk bytes we've already
        // consumed. `start` is the index of the next unread char in
        // `input`; the slice at the end carries the trailing partial
        // line into `pending` for the next chunk.
        let start = 0;
        let idx;
        while ((idx = input.indexOf('\n', start)) >= 0) {
            const line = input.slice(start, idx);
            start = idx + 1;
            if (!line) continue;
            try {
                queue.push(JSON.parse(line) as FoundryEvent);
            } catch {
                queue.push({
                    type: 'error',
                    message: `Failed to parse event: ${line.slice(0, 200)}`,
                    kind: 'protocol',
                });
            }
        }
        pending = start < input.length ? input.slice(start) : '';
    };

    child.stdout.on('data', (chunk: Buffer) => {
        parseLines(pending + stdoutDecoder.write(chunk));
        if (queue.length >= HIGH_WATER && !child.stdout.isPaused()) {
            child.stdout.pause();
        }
        wake();
    });

    child.on('error', (err) => {
        exitError = err;
        closed = true;
        wake();
    });

    child.on('close', (code) => {
        // Flush any final bytes still buffered in the decoder + the
        // trailing partial line, in case the producer didn't end with \n.
        const tail = pending + stdoutDecoder.end();
        if (tail) parseLines(tail + '\n');
        if (code !== 0 && !queue.some(e => e.type === 'done' || e.type === 'error')) {
            queue.push({
                type: 'error',
                message: stderrBuffer.trim() || `Foundry exited with code ${code}`,
                kind: 'process',
            });
        }
        closed = true;
        wake();
    });

    // Pull events out of the queue until we see a terminal event or
    // the child closes. The try/finally guarantees the child is reaped
    // even if the consumer breaks early or throws.
    try {
        while (true) {
            if (queue.length > 0) {
                const evt = queue.shift()!;
                if (queue.length < LOW_WATER && child.stdout.isPaused()) {
                    child.stdout.resume();
                }
                yield evt;
                if (evt.type === 'done' || evt.type === 'error') {
                    // Drain any same-tick siblings, then return — no
                    // point waiting on the close handler when the
                    // protocol already said we're done.
                    while (queue.length > 0) yield queue.shift()!;
                    return;
                }
            } else if (closed) {
                break;
            } else {
                await new Promise<void>((resolve) => { resolveNext = resolve; });
            }
        }

        if (exitError) {
            throw exitError;
        }
    } finally {
        if (child.exitCode === null && child.signalCode === null) {
            try { child.kill('SIGTERM'); } catch { /* already dead */ }
        }
    }
}
