# Mesh Phase 1: Tool-Contribution Wire — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Crowe Terminal's local agent tools (`:8012` — `editor.get_active_context`, `terminal.exec_safe`, etc.) callable by Foundry's agent loop from *any* Foundry entry point, not just when Crowe Terminal spawns the bridge with the auth key in env.

**Architecture:** Crowe Terminal's Electron main generates a random `AuthKey`, passes it via `WAVETERM_AUTH_KEY` to the Go backend, which reads it into memory and **unsets the env var** (`pkg/authkey/authkey.go`). The Go agent server at `:8012` validates every request's `X-AuthKey` against that in-memory key. Foundry's `tools/crowe_terminal.py` already does full discovery + exec-built proxies + cache busting, but `_headers()` only reads `WAVETERM_AUTH_KEY` from env — so a standalone `crowe-logic run`, Cortex's `crowe-logic headless` subprocess, or the control plane cannot authenticate and gets `401`. **Fix: Crowe Terminal persists the key to `~/.crowe-logic/agent-authkey` (0600) when the agent server starts; Foundry reads env first, then falls back to that token file.** This unfractures the two agent registries over the existing seam with no protocol change.

**Tech Stack:** Go 1.25 (crowe-terminal, `pkg/authkey`, `pkg/agent`), Python 3.11 + pytest + httpx (crowe-logic-foundry, `tools/crowe_terminal.py`).

**Two repos touched:**
- `~/Projects/crowe-terminal` (Go) — writes the token file.
- `~/Projects/crowe-logic-foundry` (Python) — reads the token file.

---

## File structure

| File | Repo | Responsibility | Action |
|---|---|---|---|
| `pkg/authkey/authkey.go` | crowe-terminal | Owns the auth key; gains shared-token-file write/remove | Modify |
| `pkg/authkey/authkey_test.go` | crowe-terminal | Unit test for the token-file write + perms | Create |
| `pkg/agent/agent.go` | crowe-terminal | Calls `WriteSharedKeyFile()` once the `:8012` server is listening | Modify (`InitAgent`, after `Server.Start`) |
| `tools/crowe_terminal.py` | crowe-logic-foundry | `_auth_key()` env→file fallback; `_headers()` uses it | Modify |
| `tests/test_crowe_terminal.py` | crowe-logic-foundry | Tests for auth resolution + discovery no-op | Create |

**Out of scope for Phase 1 (tracked for later phases):** re-discovery when Terminal starts *after* a long-running Foundry imports tools (import-time discovery only — fine for per-invocation `crowe-logic run`/`headless`); merging the two registries; CMP protocol extraction (Phase 2). Note these in the PR description; do not implement here.

---

## Task 0: Branch setup in both repos

- [ ] **Step 1: Branch crowe-terminal**

```bash
cd ~/Projects/crowe-terminal && git checkout -b feat/mesh-phase1-shared-authkey
```

- [ ] **Step 2: Branch crowe-logic-foundry**

The Phase 1 implementation is independent of the design docs, so branch it off `main` to keep it minimal and PR-able on its own. The spec/plan live on `docs/crowe-agent-mesh-spec`; the parallel-session Kimi work (`crowelm-hyphae-nexus`) is committed on `feat/crowelm-hyphae-nexus`. **Before switching branches, re-run the collision check (Step 3 below) — a parallel session shares this working tree.** With a clean tree:

```bash
cd ~/Projects/crowe-logic-foundry && git checkout main && git checkout -b feat/mesh-phase1-token-auth
```

- [ ] **Step 3: Collision check (shared working tree)**

This repo is edited by parallel Claude sessions; `config/agent_config.py` is the flagged high-collision file. Before any branch/stash/reset, confirm the tree is clean and no other session is mid-git:

```bash
cd ~/Projects/crowe-logic-foundry && git status --short && git branch --show-current
```

Expected: no modified (` M`) files you did not author. If you see unexpected modifications, STOP and coordinate — do not stash or reset.

---

## Task 1: crowe-terminal — `authkey` gains shared-token-file write/remove

**Files:**
- Modify: `~/Projects/crowe-terminal/pkg/authkey/authkey.go`
- Test: `~/Projects/crowe-terminal/pkg/authkey/authkey_test.go` (create)

- [ ] **Step 1: Write the failing test**

Create `~/Projects/crowe-terminal/pkg/authkey/authkey_test.go`:

```go
// Copyright 2026, Crowe Logic Inc.
// SPDX-License-Identifier: Apache-2.0

package authkey

import (
	"os"
	"path/filepath"
	"testing"
)

func TestWriteSharedKeyFileWritesKeyWith0600(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	authkey = "test-key-123"
	t.Cleanup(func() { authkey = "" })

	if err := WriteSharedKeyFile(); err != nil {
		t.Fatalf("WriteSharedKeyFile: %v", err)
	}

	path, err := sharedKeyFilePath()
	if err != nil {
		t.Fatalf("sharedKeyFilePath: %v", err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read token file: %v", err)
	}
	if string(data) != "test-key-123" {
		t.Fatalf("token file = %q, want %q", string(data), "test-key-123")
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatalf("stat token file: %v", err)
	}
	if perm := info.Mode().Perm(); perm != 0o600 {
		t.Fatalf("token file perms = %o, want 600", perm)
	}
	if dir := filepath.Base(filepath.Dir(path)); dir != ".crowe-logic" {
		t.Fatalf("token dir = %q, want .crowe-logic", dir)
	}
}

func TestWriteSharedKeyFileErrorsWhenKeyUnset(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	authkey = ""
	if err := WriteSharedKeyFile(); err == nil {
		t.Fatal("expected error when auth key is unset, got nil")
	}
}

func TestRemoveSharedKeyFileIsIdempotent(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	if err := RemoveSharedKeyFile(); err != nil {
		t.Fatalf("RemoveSharedKeyFile on absent file should be nil, got %v", err)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run (from repo root, per crowe-terminal rules — do not `cd` into the package):

```bash
cd ~/Projects/crowe-terminal && go test ./pkg/authkey/ -run 'TestWriteSharedKeyFile|TestRemoveSharedKeyFile' -v
```

Expected: FAIL — `undefined: WriteSharedKeyFile`, `undefined: sharedKeyFilePath`, `undefined: RemoveSharedKeyFile`.

- [ ] **Step 3: Write minimal implementation**

In `~/Projects/crowe-terminal/pkg/authkey/authkey.go`, add `"path/filepath"` to the import block (alongside `"fmt"`, `"net/http"`, `"os"`), then append these functions at the end of the file:

```go
const sharedKeyFileName = "agent-authkey"

func sharedKeyFilePath() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".crowe-logic", sharedKeyFileName), nil
}

// WriteSharedKeyFile persists the in-memory auth key to ~/.crowe-logic/agent-authkey
// (0600) so out-of-process Crowe Logic surfaces (Foundry, Cortex) can authenticate to
// the loopback agent transport without inheriting WAVETERM_AUTH_KEY from the environment.
func WriteSharedKeyFile() error {
	if authkey == "" {
		return fmt.Errorf("auth key not set; call SetAuthKeyFromEnv first")
	}
	path, err := sharedKeyFilePath()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	return os.WriteFile(path, []byte(authkey), 0o600)
}

func RemoveSharedKeyFile() error {
	path, err := sharedKeyFilePath()
	if err != nil {
		return err
	}
	if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
		return err
	}
	return nil
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd ~/Projects/crowe-terminal && go test ./pkg/authkey/ -run 'TestWriteSharedKeyFile|TestRemoveSharedKeyFile' -v
```

Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/crowe-terminal && git add pkg/authkey/authkey.go pkg/authkey/authkey_test.go && git commit -m "$(cat <<'EOF'
feat(authkey): persist shared auth key to ~/.crowe-logic/agent-authkey

Lets out-of-process Crowe Logic surfaces (Foundry, Cortex) authenticate to the
:8012 agent transport without inheriting WAVETERM_AUTH_KEY from env.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: crowe-terminal — write the token file when `:8012` starts

**Files:**
- Modify: `~/Projects/crowe-terminal/pkg/agent/agent.go` (`InitAgent`, after `Server.Start(ctx)` succeeds)

No new test: this is one wiring call into a function already unit-tested in Task 1, exercised end-to-end in Task 5. Per crowe-terminal rules, compilation is verified by the editor's problems panel, not `go build`.

- [ ] **Step 1: Add the import**

In `~/Projects/crowe-terminal/pkg/agent/agent.go`, add `"github.com/wavetermdev/waveterm/pkg/authkey"` to the import block (keep imports grouped/sorted; it sorts after `agenthttp`).

- [ ] **Step 2: Write the token file once the server is listening**

In `InitAgent`, the block that starts the server currently reads:

```go
	Server = agenthttp.MakeServer(host, port, Hub)
	if err := Server.Start(ctx); err != nil {
		log.Printf("[agent] failed to start transport: %v\n", err)
		Server = nil
		return
	}
	terminal.SetEventHub(Hub)
	log.Printf("[agent] ready on http://%s/ (tools=%d)\n", Server.Addr(), toolCount())
```

Insert the token-file write immediately after the successful `Start`, before `terminal.SetEventHub`:

```go
	Server = agenthttp.MakeServer(host, port, Hub)
	if err := Server.Start(ctx); err != nil {
		log.Printf("[agent] failed to start transport: %v\n", err)
		Server = nil
		return
	}
	if err := authkey.WriteSharedKeyFile(); err != nil {
		log.Printf("[agent] could not write shared auth key file: %v\n", err)
	}
	terminal.SetEventHub(Hub)
	log.Printf("[agent] ready on http://%s/ (tools=%d)\n", Server.Addr(), toolCount())
```

(`authkey.GetAuthKey()` is already populated by this point: `cmd/server/main-server.go:413` calls `authkey.SetAuthKeyFromEnv()` in `grabAndRemoveEnvVars()` during early server startup, before `InitAgent` runs. A write failure is logged and non-fatal — the env-var path still works for the bridge-spawned case.)

- [ ] **Step 3: Verify it compiles**

Open `pkg/agent/agent.go` in the editor and confirm the problems panel shows no errors (per project rules, do not run `go build`).

- [ ] **Step 4: Commit**

```bash
cd ~/Projects/crowe-terminal && git add pkg/agent/agent.go && git commit -m "$(cat <<'EOF'
feat(agent): write shared auth key file when :8012 transport starts

InitAgent persists the auth key after the loopback agent server is listening so
any Foundry entry point can read it. Non-fatal on failure.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: crowe-logic-foundry — `_auth_key()` env→file fallback (TDD)

**Files:**
- Modify: `~/Projects/crowe-logic-foundry/tools/crowe_terminal.py`
- Test: `~/Projects/crowe-logic-foundry/tests/test_crowe_terminal.py` (create)

- [ ] **Step 1: Write the failing test**

Create `~/Projects/crowe-logic-foundry/tests/test_crowe_terminal.py`:

```python
from tools import crowe_terminal as ct


def test_auth_key_prefers_env(monkeypatch, tmp_path):
    monkeypatch.setenv("WAVETERM_AUTH_KEY", "env-key")
    token = tmp_path / "agent-authkey"
    token.write_text("file-key")
    monkeypatch.setattr(ct, "_TOKEN_FILE", token)
    assert ct._auth_key() == "env-key"


def test_auth_key_falls_back_to_token_file(monkeypatch, tmp_path):
    monkeypatch.delenv("WAVETERM_AUTH_KEY", raising=False)
    token = tmp_path / "agent-authkey"
    token.write_text("file-key\n")
    monkeypatch.setattr(ct, "_TOKEN_FILE", token)
    assert ct._auth_key() == "file-key"


def test_auth_key_none_when_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("WAVETERM_AUTH_KEY", raising=False)
    monkeypatch.setattr(ct, "_TOKEN_FILE", tmp_path / "missing")
    assert ct._auth_key() is None


def test_headers_include_authkey_from_file(monkeypatch, tmp_path):
    monkeypatch.delenv("WAVETERM_AUTH_KEY", raising=False)
    token = tmp_path / "agent-authkey"
    token.write_text("file-key")
    monkeypatch.setattr(ct, "_TOKEN_FILE", token)
    headers = ct._headers()
    assert headers["X-AuthKey"] == "file-key"
    assert headers["Content-Type"] == "application/json"


def test_headers_omit_authkey_when_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("WAVETERM_AUTH_KEY", raising=False)
    monkeypatch.setattr(ct, "_TOKEN_FILE", tmp_path / "missing")
    assert "X-AuthKey" not in ct._headers()


def test_discover_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("CROWE_AGENT_TOOLS", raising=False)
    assert ct.discover_and_register() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run (use the repo's venv directly — the `.zshrc` PATH hook does not fire in non-interactive shells):

```bash
cd ~/Projects/crowe-logic-foundry && .venv/bin/python -m pytest tests/test_crowe_terminal.py -v
```

Expected: FAIL — `AttributeError: module 'tools.crowe_terminal' has no attribute '_TOKEN_FILE'` / `_auth_key`.

- [ ] **Step 3: Write minimal implementation**

In `~/Projects/crowe-logic-foundry/tools/crowe_terminal.py`:

(a) Add `from pathlib import Path` to the imports near the top (alongside `import os`).

(b) Add the token-file constant next to the existing env-name constants (after `AUTH_ENV = "WAVETERM_AUTH_KEY"`):

```python
_TOKEN_FILE = Path.home() / ".crowe-logic" / "agent-authkey"
```

(c) Add the `_auth_key()` helper directly above `_headers()`:

```python
def _auth_key() -> Optional[str]:
    """Resolve the agent transport auth key: env first, then the shared token file.

    Crowe Terminal sets WAVETERM_AUTH_KEY when it spawns the bridge directly; for every
    other Foundry entry point it persists the key to ~/.crowe-logic/agent-authkey.
    """
    key = os.environ.get(AUTH_ENV)
    if key:
        return key
    try:
        return _TOKEN_FILE.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None
```

(d) Replace the body of `_headers()` to use it. Current:

```python
def _headers() -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = os.environ.get(AUTH_ENV)
    if key:
        headers["X-AuthKey"] = key
    return headers
```

New:

```python
def _headers() -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = _auth_key()
    if key:
        headers["X-AuthKey"] = key
    return headers
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd ~/Projects/crowe-logic-foundry && .venv/bin/python -m pytest tests/test_crowe_terminal.py -v
```

Expected: PASS (6 tests).

- [ ] **Step 5: Confirm no regression in tool loading**

```bash
cd ~/Projects/crowe-logic-foundry && .venv/bin/python -m pytest -q
```

Expected: the suite's prior pass/fail baseline is unchanged (note: `tests/test_nemoclaw.py::test_talon_alias_resolves_to_nemoclaw_provider` is a known pre-existing failure per the doctor-and-rebrand memory — it is unrelated to this change).

- [ ] **Step 6: Commit**

```bash
cd ~/Projects/crowe-logic-foundry && git add tools/crowe_terminal.py tests/test_crowe_terminal.py && git commit -m "$(cat <<'EOF'
feat(crowe_terminal): read agent auth key from shared token file

Falls back to ~/.crowe-logic/agent-authkey when WAVETERM_AUTH_KEY is absent, so the
agent loop can reach Crowe Terminal's :8012 tools from any Foundry entry point
(standalone CLI, headless, control plane), not just the bridge-spawned path.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: End-to-end verification (manual, real processes)

This proves the acceptance criteria from the spec. Requires building/running Crowe Terminal locally.

- [ ] **Step 1: Build and launch Crowe Terminal**

```bash
cd ~/Projects/crowe-terminal && task electron:quickdev
```

Wait for the window. In its logs, confirm a line like `[agent] ready on http://127.0.0.1:8012/ (tools=N)`.

- [ ] **Step 2: Confirm the token file was written**

```bash
ls -l ~/.crowe-logic/agent-authkey && stat -f '%Sp' ~/.crowe-logic/agent-authkey
```

Expected: file exists, permissions `-rw-------` (600).

- [ ] **Step 3: Open a Crowe Code block and focus an editor**

In the running Terminal, `+` → "Crowe Code", open any file, click into the editor and place the cursor on a line (this populates `editorctx` via `CroweCodeReportActiveEditorCommand`).

- [ ] **Step 4: From a SEPARATE shell (no `WAVETERM_AUTH_KEY` in env), list tools**

```bash
cd ~/Projects/crowe-logic-foundry && env -u WAVETERM_AUTH_KEY CROWE_AGENT_TOOLS=1 .venv/bin/crowe-logic tools list | grep -E '^ct_' | head
```

Expected: `ct_editor_get_active_context`, `ct_terminal_exec_safe`, `ct_system_metrics`, etc. appear. (Before this change, with no env key, the list would be empty — the catalog probe 401'd.)

- [ ] **Step 5: Prove a real agent turn calls the tools**

```bash
cd ~/Projects/crowe-logic-foundry && env -u WAVETERM_AUTH_KEY CROWE_AGENT_TOOLS=1 .venv/bin/crowe-logic run "Use your Crowe Terminal tools: report my current editor context, then run 'git status' read-only in the terminal."
```

Expected: the turn calls `ct_editor_get_active_context` (returns the focused file + cursor/selection) and `ct_terminal_exec_safe` (returns `git status` output). Errors like "command refused: matches mutating denylist" for a write command are correct behavior, not failures.

- [ ] **Step 6: Negative check — flag off / Terminal closed is a clean no-op**

Quit Crowe Terminal, then:

```bash
cd ~/Projects/crowe-logic-foundry && env -u WAVETERM_AUTH_KEY .venv/bin/crowe-logic tools list | grep -c '^ct_'
```

Expected: `0`, with no traceback (catalog probe fails silently when the server is down).

- [ ] **Step 7: Record results**

If all steps pass, the Phase 1 acceptance criteria in `docs/superpowers/specs/2026-05-23-crowe-agent-mesh-design.md` are met. Note any deviations in the PR description.

---

## Acceptance criteria (from spec, restated)

- [ ] `GET /v1/tools` on `:8012` is consumed by Foundry; `ct_*` tools appear in `crowe-logic tools list` when the flag is on and Terminal is running — **without** `WAVETERM_AUTH_KEY` in env (Task 4 Step 4).
- [ ] An agent turn calls `editor.get_active_context` and receives a real snapshot of the focused Crowe Code editor (Task 4 Step 5).
- [ ] An agent turn calls `terminal.exec_safe` and receives real output (Task 4 Step 5).
- [ ] Terminal not running or flag off → no tools, no errors (Task 4 Step 6).
- [ ] Existing tests pass; new tests cover discovery no-op, token-file fallback, env precedence, and header omission (Tasks 1 & 3).
