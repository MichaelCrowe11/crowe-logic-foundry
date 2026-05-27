# Crowe Logic Foundry — Agent Context

You are operating inside the **Crowe Logic Foundry** monorepo.

## Project Identity
- **Name**: Crowe Logic Foundry
- **Language**: Python 3.10+ (backend), Node/TypeScript (tooling & distribution)
- **License**: Proprietary
- **Registry**: GitHub Packages (`@michaelcrowe11/crowe-logic`)

## Architecture
```
crowe-logic-foundry/
├── cli/               # Python CLI entrypoint (crowe_logic.py)
├── control_plane/     # FastAPI control plane (:8001)
├── agents/            # YAML agent definitions (studio, quantum, cultivation, etc.)
├── tools/             # Agent runners, staging pipeline, audit log
├── config/            # Tenant registries, model configs
├── crowe_synapse_engine/  # Azure AI Foundry inference engine
├── scripts/           # Admin, bootstrap, migration scripts
├── deploy/            # Railway, Azure ML, IDE extensions
├── npm/               # Node wrapper for PyPI distribution
├── .pi/               # Pi coding-agent integration
└── Makefile           # Developer build targets
```

## Key Conventions
1. **Python**: `ruff` for lint/format, `pytest` for tests, `.venv` for env
2. **Agents**: Defined in YAML under `agents/`. Never hardcode tenant names — read from `config/studio_tenants.yaml`.
3. **CLI**: `python -m cli.crowe_logic <subcommand>` or `make chat`
4. **Build**: `make install` → `make lint` → `make test` → `make preview`
5. **Deploy**: Railway for control plane, Azure AI Foundry for model endpoints
6. **Security**: `.env` is gitignored. Use `scripts/issue_tester_key.py` for local keys.
7. **CroweLM**: The custom model stack. Pipeline agents run via `tools/agent_runner.py`.

## When to Use Custom Tools
- Use `crowe_build` for Makefile targets (lint, test, preview, e2e)
- Use `crowe_logic` for CLI subcommands (agents, pipelines, chat)
- Use `crowe_agent` for CroweLM pipeline runs (data generation, curation)
- Use `crowe_config` to inspect `.env` or `pyproject.toml` safely

## Build Flow
```
make install      # venv + pip install -e . + dev deps
make lint         # ruff check .
make fmt          # ruff format + check --fix
make test         # pytest -q
make pi-review    # pi-powered code review (read-only tools)
make pi-build     # lint → pi-review → test (enhanced gate)
make preview      # SQLite control plane on :8001
make prod         # uvicorn reload on :8001
make e2e          # smoke test (preview + key + gateway call)
```

## Danger Zones
- Never write to `.env`, `.env.local`, or `node_modules/`
- Never run `rm -rf` on project dirs without confirmation
- Railway secrets are set via `railway variables`, not committed
- Stripe live keys are in `.env` only
