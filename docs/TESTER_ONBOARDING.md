# Tester Onboarding — Crowe Logic Foundry

Welcome. You've been granted early access to the **Crowe Logic** gateway
(CroweLM Nano / Forge / Titan / Apex / Prime / …) through an issued API
key of the form `cl_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`.

This doc covers the full app-development pipeline from zero → producing
model responses inside VS Code.

---

## 1. Prerequisites

- macOS or Linux
- Python ≥ 3.11 (3.14 recommended)
- [VS Code](https://code.visualstudio.com/)
- `git`, `make`, `curl`

## 2. Clone & open

```bash
git clone https://github.com/MichaelCrowe11/crowe-logic-foundry
cd crowe-logic-foundry
code .
```

When VS Code opens it will prompt you to install the **recommended
extensions** from `.vscode/extensions.json` (Python + Pylance + Ruff +
Docker + Azure tools + REST Client + Copilot). Accept them all.

## 3. Install

```bash
make install        # creates .venv and installs everything
```

VS Code should now auto-select `.venv/bin/python` as the interpreter
(confirm in the status bar).

## 4. Set your Crowe AI key

Your operator will hand you a key. Add it to a local `.env.local`
(already gitignored) or export it per-session:

```bash
export CROWE_LOGIC_KEY=cl_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
export CROWE_CONTROL_PLANE_URL=https://foundry.crowelogic.com   # or http://127.0.0.1:8001 for local preview
```

## 5. Run a request — three ways

### (a) From the VS Code REST Client
Open `.vscode/crowe-logic.http`, replace `@key` with your issued key,
and click **Send Request** above any block.

### (b) From `curl`
```bash
curl -H "Authorization: Bearer $CROWE_LOGIC_KEY" \
     -H "Content-Type: application/json" \
     -d '{"model":"gpt-5.4-nano","messages":[{"role":"user","content":"Hello"}]}' \
     $CROWE_CONTROL_PLANE_URL/api/gateway/chat
```

### (c) From the Crowe Logic CLI
```bash
make chat          # interactive TUI
# or:
.venv/bin/python -m cli.crowe_logic run "Hello, Crowe Logic"
```

## 6. Local preview (offline development)

If you want to develop against a local gateway without hitting the
production control plane:

```bash
make preview       # starts SQLite-backed gateway on :8001
make key L=my-dev P=lab    # mints a cl_… key wired to that DB
# copy the printed raw key into CROWE_LOGIC_KEY
```

Swagger UI: <http://localhost:8001/docs>

## 7. Debugging from VS Code

Use **Run and Debug** (⇧⌘D). Available launch configs:

- *Crowe Logic: Chat (CLI)* — breakpoints inside `cli/crowe_logic.py`
- *Crowe Logic: Run prompt* — single-shot
- *Control Plane: Preview (SQLite)* — debug gateway/routers
- *Control Plane: Production (uvicorn)* — reloads on save
- *Pytest: current file*
- *Issue tester key (local SQLite preview)*

## 8. Tests & lint

```bash
make test          # pytest -q      (baseline: 281 passed)
make lint          # ruff check
make fmt           # ruff format + fix
```

## 9. Model access tiers

Your workspace is on plan `lab` by default. That gives you:

| Tier         | Representative models                                    |
|--------------|----------------------------------------------------------|
| `developer`  | `gpt-5.4-nano`, `Llama-3-3-70B`, `FW-GLM-5`              |
| `studio`     | `Kimi-K2.5`, `DeepSeek-R1/V3-1`, `Mistral-Large-3`       |
| `lab` ✓      | `gpt-5.4`, `claude-opus-4-6`                             |
| `enterprise` | `gpt-5.4-pro`, `grok-4-20-reasoning`, `claude-opus-4-5`  |

Hit `GET /api/gateway/models` (with your key) to see the live catalog.

## 10. Getting help

- `.chat_history` contains prior sessions — never commit it (gitignored).
- File issues at the GitHub repo.
- Ping Michael Crowe directly for plan/tier upgrades or per-model access.

Happy building. 🍄
