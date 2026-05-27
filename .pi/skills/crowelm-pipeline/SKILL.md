# CroweLM Pipeline Skill

Use this skill when the user wants to run CroweLM data pipeline agents, curate training examples, or manage the staging pipeline.

## Prerequisites
- `.venv` must exist and be activated (or use `.venv/bin/python` directly)
- `data/crowelm-unified/staging/` directories should exist (auto-created on first run)
- Environment variables: `AGENT_NAME`, `AGENT_TASK` for headless runs

## Steps

1. **Determine agent and task**
   - List available agents: `crowe_logic` tool with command `agents`
   - Or check `agents/` directory for YAML definitions
   - Common pipeline agents: `crowelm_gen_mycology`, `crowelm_gen_cultivation`, `crowelm_gen_research`

2. **Run the agent**
   - Local mode (default): use `crowe_agent` tool with mode `local`
   - Docker mode: use mode `docker`
   - Example:
     ```json
     { "agent": "crowelm_gen_mycology", "task": "Generate 50 training examples for Psilocybe azurescens cultivation", "mode": "local" }
     ```

3. **Check pipeline status**
   - Staging dirs: `data/crowelm-unified/staging/{pending,approved,review,rejected}/`
   - Audit logs: `data/crowelm-unified/logs/`
   - Reports: `data/crowelm-unified/reports/`

4. **Promote approved examples**
   - After agent run completes, call the staging pipeline:
     ```bash
     .venv/bin/python -c "from tools.staging_pipeline import promote_approved; promote_approved()"
     ```

5. **Create PR (CI context)**
   - Branch: `crowelm/batch-YYYYMMDD-HHMMSS`
   - Commit message: `feat(crowelm): approved training examples from pipeline run`
   - Push and open PR via `gh pr create`

## Safety
- Pipeline agents run in isolated subprocesses with restricted env vars
- No `.env`, API keys, or git credentials are passed to agent subprocesses
- Always review `data/crowelm-unified/reports/` before promoting

## Troubleshooting
- If agent fails with "Invalid agent_id", check the YAML name matches exactly
- If staging dirs are missing, run: `.venv/bin/python -c "import tools.staging_pipeline as sp; sp.ensure_staging_dirs()"`
- For timeouts, use Docker mode or increase timeout in `tools/agent_runner.py`
