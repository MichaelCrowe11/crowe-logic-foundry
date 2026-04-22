# Crowe Logic Foundry: Developer Report

Status: canonical. Written 2026-04-21. Covers the state of the codebase
after the April 2026 "observation deck" pass and the preceding dual-mode
and NemoClaw passes.

## System thesis

Crowe Logic Foundry is a multi-model agent CLI. One operator, many
backends, one consistent surface. The product differentiator is running
two premium models concurrently against the same prompt and synthesizing
their output, plus a sandboxed shell (NemoClaw) for code execution that
can't reach the operator's filesystem, plus a tool registry that spans
web search, browser automation, MCP, iTerm2 control, Arizona public
records, and Crowe Logic's own platform APIs.

The whole thing runs as a Click-driven Python CLI that speaks to seven
provider kinds (anthropic, azure_openai, nvidia, watsonx, openai_compat,
openrouter, ollama) through a thin provider abstraction. Rendering is
Rich. Input is prompt_toolkit. State is split between an in-memory
session dict and a SQLite `MemoryStore` for long-term recall.

## Architecture at a glance

```
  User
   │
   ▼
  cli/crowe_logic.py  (Click entry + chat loop)
   │
   ├─► cli/dual_mode.py       ── spawns two provider workers per turn
   │     │
   │     ├─► QueueRenderer ──► event queue ──► DualPaneRenderer ──► Rich Live
   │     │
   │     └─► post-stream synth turn (optional)
   │
   ├─► providers/{anthropic,azure_openai,nvidia,watsonx,openai_compat,openrouter,ollama}.py
   │     │
   │     └─► providers/_shared.py  (BaseOpenAIProvider, tool-calling loop)
   │
   ├─► tools/*.py  (98 registered functions)
   │     │
   │     └─► tools/nemoclaw.py  ── sandboxed shell over HTTP to a Brev VM
   │
   ├─► cli/cost_model.py  ── upstream USD + customer credits
   │     └─► config/upstream_costs.json + customer_pricing.json
   │
   ├─► cli/history.py     ── indexed turn log for /replay and /fork
   │
   └─► crowe_synapse_engine/  ── long-term memory (SQLite/WAL, thread-safe)
```

## What shipped this session

Nine commits on main in roughly this order:

| Commit | Title | Delta |
|---|---|---|
| 8b5110d | Dual-mode side-by-side, tool-arg resilience, Crescent/Eclipse | 2,277 insertions |
| d5254a8 | Thread-safe memory, Ollama paywall detection, fallback chain | 330 insertions |
| d7ea7e8 | Crowe Talon on NVIDIA NemoClaw with Brev Launchable | 1,139 insertions |
| 8144eaa | Synthesis pane with merge, judge, diff modes | 222 insertions |
| 216b1d0 | SessionCostTracker, Crescent/Eclipse branding | mixed |
| 45a9267 | Attach cost tracker lazily, surface HUD cost/credits | mixed |
| d5c6d71 | Wire tracker into single + dual turn completion | 100 insertions |
| b026ddd | Anthropic prompt caching + usage publishing | 96 insertions |
| 31dfba6 | /replay and /fork with indexed turn history | 296 insertions |

Net: about 4,460 lines of new Python plus 12 config and documentation
files. Every commit pushed to `origin/main`.

## Key subsystems

### Dual mode (`cli/dual_mode.py`, `cli/dual_renderer.py`, `cli/queue_renderer.py`)

The architectural constraint Rich imposes: one `Live` widget per terminal.
Solution is a producer-consumer with a single consumer on the main thread
and two producers in worker threads. `QueueRenderer` implements the same
interface as `StreamRenderer` but forwards every lifecycle call as a
`PaneEvent` onto a shared `queue.Queue`. `DualPaneRenderer` owns the one
Rich `Live` + `Layout` split 50/50 and drains events at 20fps, routing
each to the correct pane.

Thread safety matters because workers hit `tools/*` functions that touch
the SQLite memory store. `crowe_synapse_engine/memory.py` was rewritten
with `_LockedConnection` and `_LockedCursor` proxies serializing every
execute and fetch through one `RLock`. Without this fix, both dual panes
raised `ProgrammingError` on turn 1.

### Tool arg resilience (`cli/tool_args.py`)

Models emit tool call JSON that isn't always strict. Unescaped newlines
in Python source payloads, Rich markup breaking brace matchers, raw
tabs. Strict `json.loads` rejects all three. `parse_tool_arguments()`
tries three progressively more lenient strategies: strict parse,
escape-recovery that walks the string inserting proper escapes, and a
regex fallback that extracts top-level `"key": "value"` pairs. Also
unwraps `content_b64` into `content` so code-heavy writes can bypass
JSON escaping entirely. Applied at every call site in providers and
the fallback Azure Agents path.

### NemoClaw (`tools/nemoclaw.py`, `agents/crowe-talon.yaml`, `launchable/`)

Crowe Talon runs on a Brev-provisioned VM that colocates NVIDIA NIM
inference with the OpenShell sandbox. Inference uses the standard
`openai_compat` provider. Shell execution proxies through
`nemoclaw_shell` to `POST /openshell/v1/exec` on the VM. Response
schema mirrors `tools/shell.py::execute_shell` so agent prompts
don't need to branch. Env contract is discovered by
`scripts/nemoclaw_recon.sh` which probes ports and paths inside the
VM, because alpha NemoClaw builds move the OpenShell API path.

Brev Launchable (`launchable/brev.yaml` + `bootstrap.sh`) packages the
whole thing for one-click provision: L40S instance, idempotent
first-boot script that pulls NIM + OpenShell + Foundry, runs recon,
prints the operator `.env` block.

### Synthesis (`cli/dual_mode.py`)

After both panes finish streaming, the synthesis turn runs stateless
on the synthesizer model (default CroweLM Supreme) with one of three
system prompts:

- **merge**: blend strongest claims, resolve contradictions, no hedging
- **judge**: pick the stronger answer in two sentences, restate clean
- **diff**: agreements, disagreements with reasoning, gaps

The synth turn's input is a framed string containing the user's
original question and both peer answers. No conversation history is
bound, so the turn is reproducible for `/replay`. Opt-in via
`/dual synth on`.

### Cost model (`cli/cost_model.py`)

Two independent concerns in one module. **Upstream cost estimation**
reads `config/upstream_costs.json` and maps (provider, backend_name) to
per-1M-token prices from Anthropic, Azure, Ollama, NIM, IBM, Moonshot,
OpenRouter. Handles caching rates (cache read, 5m write, 1h write) and
subscription-based models (Ollama Pro zeroed at the token level with
the $20/month fee attributed separately). **Customer credit
accounting** reads `config/customer_pricing.json` and maps model tier
(flagship, balanced, fast) to credits consumed per turn. Dual mode
sums both sides. Synthesis adds 5. Tool calls free up to 10 per turn,
overage at 1 credit per 10.

`SessionCostTracker` accumulates across a CLI session, thread-safe for
the dual-mode worker case. `_record_turn_telemetry` in
`cli/crowe_logic.py` converges both single-model and dual-mode
success paths into one call that records a `TurnCost` plus
`CreditCost` per completed turn.

### Anthropic prompt caching (`providers/anthropic.py`)

The system block and the tools array get `cache_control: ephemeral`
annotations so Anthropic caches both with a 5-minute TTL. Cold turn
pays 1.25x base input on writes, subsequent turns in the window read
at 0.1x. Opus 4.7 system-prompt input cost drops from $5/MTok to
$0.50/MTok. TTFT on cached prefix improves by about half.

Critical detail: `_build_tool_schemas` now sorts tools by name so the
98-tool schema payload is byte-identical across CLI process restarts.
Without the sort, iteration order of the `user_functions` set changes
per process, which invalidates the cache on every new session.

`_publish_usage` captures real token counts off `message_start` and
`message_delta` events (`input_tokens`, `cache_creation_input_tokens`,
`cache_read_input_tokens`, `output_tokens`) and pushes them to
`session_state` so the telemetry hook records accurate cost instead
of output-only estimates.

### /replay and /fork (`cli/history.py`)

Thread-safe append-only log indexed from 1. `/replay N` re-runs past
turn N on the current model. `/replay N alias` switches model first.
`/fork N` truncates turns N+1..end so the replay overwrites the tail.
Primary use case is A/B testing: ask once, switch model, replay,
compare. Implementation reuses the chat loop's normal dispatch path;
the command handler rewrites `user_input` to the historical value
and returns, which means replayed turns flow through the same
telemetry, cost recording, and history append as fresh turns.

Deliberately decoupled from the `MemoryStore` SQLite history. That
layer is for long-term cross-session recall. `TurnHistory` is
ephemeral session state purpose-built for replay affordances.

## Model registry

Seven model configurations plus one Auto router at the top of the
chain. Aliases let users reach models by short names (`crescent`,
`eclipse`, `supreme`, `talon-nemoclaw`). `provider_model_name()` now
supports `${ENV_VAR}` interpolation in `backend_name` so NemoClaw can
late-bind the model id discovered by recon script at runtime.

The chain is layered: `_BASE_MODEL_CHAIN` in `config/agent_config.py`
is source-controlled defaults, `config/models.extra.json` is local
overrides. `/model resolve <alias>` diagnostic command prints what
any alias currently maps to (label, provider, backend, aliases) so
the layering isn't opaque.

## Test coverage

| Suite | Tests | Scope |
|---|---|---|
| `tests/test_cost_model.py` | 29 | Upstream math, caching, subscription, credits, margin reports, tracker thread safety |
| `tests/test_nemoclaw.py` | 23 | Shell tool, health probe, 404 hint, auth headers, truncation, alias resolution, env interpolation |
| `tests/test_history.py` | 9 | Indexing, truncate-after, concurrent appends, singleton |

Plus 212 pre-existing tests. 9 of those were already red before this
session and still are: `tests/test_model_config.py` and
`tests/test_cli_model_switch.py` assert retired aliases (`gpt-5.4`,
`crowelm-pro`) against the reshuffled chain. Cheap to fix but not
caused by this pass.

## Dependencies

Runtime, all already in the foundry:

- `anthropic` SDK for native Anthropic API calls
- `openai` SDK for all OpenAI-compatible providers
- `rich` for every rendered widget
- `prompt_toolkit` for input
- `httpx` for NemoClaw sandbox calls
- `requests` for DeepParallel and Ollama probes
- `click` for the CLI
- `pyyaml` for agent profile parsing

External services the Foundry talks to:

- Anthropic (Opus 4.7, Sonnet 4.6, Haiku 4.5 via Azure AI Foundry
  native Anthropic surface)
- Azure OpenAI (GPT-5.4, Azure resource 4667 primary)
- NVIDIA NIM (free dev tier, rate-limited)
- Ollama Cloud (`:cloud` tags via $20/mo Pro subscription)
- IBM watsonx (Granite-4)
- OpenRouter (pass-through credits)
- Moonshot platform (Kimi K2.5 and K2.6 direct)
- Stripe (control plane billing, not exercised from the CLI)

## Known limitations

Intentionally deferred, will be addressed in Wave 2:

1. **OpenAI and NIM prompt caching.** Both providers support caching
   with different APIs. Current caching only wraps Anthropic.
2. **Session search.** `/transcript` shows the last turn but there is
   no `/search <pattern>` across history.
3. **Cost-aware Auto routing.** Current Auto router picks by task
   classification. Could save 20-30% by preferring cheaper tiers
   when task class permits.
4. **Inline file edit diffs.** Tool cards show byte counts, not the
   actual diff.
5. **Command palette.** Thirty-plus slash commands; fuzzy search
   over them would flatten the learning curve.
6. **OpenAI-compat usage publishing.** Only Anthropic reports real
   token counts back into `session_state`. Other providers still
   fall back to the output-only estimate.

## Deploy surface

- GitHub: `MichaelCrowe11/crowe-logic-foundry`, main branch ships
  the current CLI
- PyPI install path: installed via `pip install -e .` to the user's
  Python 3.11. Binary at `~/Library/Python/3.11/bin/crowe-logic`
- Brev Launchable: `launchable/brev.yaml` + `bootstrap.sh`, provisions
  Talon/NemoClaw on a fresh VM
- Control plane: FastAPI app in `control_plane/` handles auth, plans,
  Stripe integration. Not exercised by the CLI today but wired for
  future subscription enforcement
- Docs: `docs/` has architecture, testing, NemoClaw integration, and
  pricing strategy
