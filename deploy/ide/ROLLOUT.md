# Rollout — Crowe Logic chat participant + branding

**Date drafted:** 2026-04-07
**Target:** ide.southwestmushrooms.com
**What ships:**
- VS Code chat participant `@crowe` backed by `cli/headless.py`
- Activity-bar views: Plan, Tool Activity
- Crowe Logic Dark theme + walkthrough as default
- product.json overrides (nameShort, defaultChatAgent)
- Multi-stage Dockerfile that builds the VSIX in-image

---

## 0. Pre-flight (run on the prod host)

- [ ] **SSH to the prod host.**
  ```
  ssh crowe-ide
  cd /opt/crowe-logic-foundry
  ```

- [ ] **Confirm code-server version is recent enough for the chat API.**
  ```
  docker compose -f deploy/ide/docker-compose.yml exec admin code-server --version
  ```
  Expected: a build that bundles VS Code 1.85+ (chat participant API stabilized in 1.85). If the bundled VS Code is older, the `@crowe` participant will silently fail to register. If unsure, check the running container's `package.json`:
  ```
  docker compose exec admin cat /usr/lib/code-server/lib/vscode/package.json | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])'
  ```

- [ ] **Snapshot the current admin container's image SHA** so rollback is one command:
  ```
  docker inspect crowe-ide-admin --format '{{.Image}}' | tee /tmp/crowe-ide-rollback-sha
  ```

- [ ] **Confirm `/opt/crowe-logic-foundry` exists on the host** and is the bind-mount source compose expects:
  ```
  ls /opt/crowe-logic-foundry/cli/headless.py
  ```
  If missing, the chat participant will fail at first invocation with `ModuleNotFoundError: cli.headless`.

- [ ] **Pull the latest source.**
  ```
  cd /opt/crowe-logic-foundry
  git fetch origin
  git status              # confirm clean
  git pull origin main
  ```

---

## 1. Build the new image

- [ ] **Build via compose so the resulting image is tagged `crowe-ide-codeserver:latest`** (the tag the session-router resolves):
  ```
  cd /opt/crowe-logic-foundry/deploy/ide
  docker compose build admin
  ```
  Watch for these markers in the build log — they prove the new code reached the image:
  - `Extension 'crowe-logic.vsix' was successfully installed.`
  - `apply-product-overrides: merged /tmp/product-overrides.json into /usr/lib/code-server/lib/vscode/product.json`

- [ ] **Verify the image tag.**
  ```
  docker image inspect crowe-ide-codeserver:latest --format 'built {{.Created}}'
  ```

---

## 2. Smoke test the image (no traffic yet)

- [ ] **Run the bundled smoke test.** All 12 assertions must pass:
  ```
  cd /opt/crowe-logic-foundry/deploy/ide
  IMAGE=crowe-ide-codeserver:latest \
  FOUNDRY_SRC=/opt/crowe-logic-foundry \
    bash scripts/smoke-test.sh
  ```
  Expected output ends with: `12 passed · 0 failed`

  **If any assertion fails, STOP.** The rollback SHA is in `/tmp/crowe-ide-rollback-sha` — re-tag it as `crowe-ide-codeserver:latest` and retry the build after fixing.

- [ ] **Spot-check the headless module hits a real model.** Requires the prod env file with API keys:
  ```
  docker run --rm -i \
    -v /opt/crowe-logic-foundry:/workspace/crowe-logic-foundry \
    --env-file /opt/crowe-logic-foundry/.env \
    -w /workspace/crowe-logic-foundry \
    crowe-ide-codeserver:latest \
    /opt/venv/bin/python3 -m cli.headless <<<'{"messages":[{"role":"user","content":"Reply with the single word: pong"}]}'
  ```
  Expected: an event stream ending in `{"type": "token", "delta": "pong"}` then `{"type": "done", ...}`. If this fails with a `config` error, the API keys aren't reaching the container — fix the env file before continuing.

---

## 3. Roll out the admin container

- [ ] **Recreate the admin service with the new image.**
  ```
  cd /opt/crowe-logic-foundry/deploy/ide
  docker compose up -d admin
  ```
  Expect compose to recreate `crowe-ide-admin` (not just "up to date").

- [ ] **Wait for the healthcheck to flip green (~30s).**
  ```
  watch -n 2 'docker inspect crowe-ide-admin --format "{{.State.Health.Status}}"'
  ```
  Expected: `healthy`. Ctrl-C once it goes green. If it stays `unhealthy` for >2 min, see Rollback.

- [ ] **Tail the admin logs for boot errors.**
  ```
  docker compose logs --tail=100 admin
  ```
  Look for: no `EACCES`, no `ModuleNotFoundError`, code-server reports a port bind on 8080.

---

## 4. Functional verification (admin instance)

- [ ] **Open the admin IDE in a browser.**
  ```
  https://ide.southwestmushrooms.com/      # if admin is publicly routed
  # OR via SSH tunnel:
  # ssh -L 10000:127.0.0.1:10000 crowe-ide
  # then http://127.0.0.1:10000/
  ```

- [ ] **Confirm the title bar reads "Crowe Logic IDE"** (proves product.json overrides applied at runtime, not just at build time).

- [ ] **Confirm the Crowe Logic activity bar icon is present** on the left sidebar (gold-tinted C glyph from `media/mark.svg`).

- [ ] **Open the chat panel** (`Ctrl/Cmd+Alt+I`) and type `@crowe hello`.
  - Expected: avatar shows the Crowe Logic mark, response streams in, telemetry footer appears (`N tokens · Nms`).
  - If `@crowe` is not in the participant list, the chat API version is too old — see Rollback.

- [ ] **Trigger a tool call** to validate the activity-bar pipe:
  ```
  @crowe list the files in the current workspace
  ```
  Expected: the Tool Activity pane on the left fills with one or more entries (`✓ list_files (...ms)`), and the same cards stream inline in the chat.

---

## 5. Roll out to subscriber containers

The session router spawns subscriber containers from `crowe-ide-codeserver:latest`. Existing sessions keep running their old image until the user reconnects.

- [ ] **Drain existing sessions** (optional but cleaner):
  ```
  cd /opt/crowe-logic-foundry/deploy/ide/session-router
  # Either restart the router so new sessions get the new image
  # immediately, or let users churn naturally over the next hour.
  docker compose restart session-router
  ```

- [ ] **Force one subscriber to use the new image** to validate end-to-end:
  ```
  # Spin up a fresh test session via the router's admin API
  # (replace with the actual route from the router's RUNBOOK):
  curl -X POST https://ide.southwestmushrooms.com/admin/sessions \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -d '{"user":"smoke-test"}'
  ```
  Open the returned URL in a private window and repeat the §4 checks.

- [ ] **Confirm zero existing-user breakage.** Tail router logs for ~5 min:
  ```
  docker compose logs -f --tail=50 session-router
  ```
  No new error patterns vs. baseline.

---

## 6. Post-deploy

- [ ] **Cleanup the rollback SHA file** once the deploy is stable for ~24h:
  ```
  rm /tmp/crowe-ide-rollback-sha
  ```

- [ ] **Update CLAUDE.md memory entry** for `crowe-logic-foundry` with the new deployment date and feature surface (only if working with the auto-memory system).

---

## Rollback (if any of §3–§5 goes wrong)

**Symptom → action:**

| Symptom | Action |
|---|---|
| Admin healthcheck stays unhealthy | `docker tag $(cat /tmp/crowe-ide-rollback-sha) crowe-ide-codeserver:latest && docker compose up -d admin` |
| `@crowe` not in chat participant list | Chat API version mismatch — rollback as above and pin a newer code-server base in `Dockerfile.code-server` (`FROM codercom/code-server:4.x.y`) |
| product.json EACCES errors in logs | Permissions bug regression — rollback and check `apply-product-overrides.sh` chmod/chown lines survived the merge |
| Subscriber containers fail to spawn | Router still references old tag — `docker compose -f session-router/docker-compose.yml restart` after retagging |
| Headless module fails to import in container | Bind mount missing — verify `/opt/crowe-logic-foundry` exists on the host and the compose `volumes:` line is intact |

Rollback is **always** safe because:
1. The session router uses image SHAs internally, not tags
2. `product.json.orig` is preserved inside the image so future rebuilds re-merge from a clean baseline
3. No database migrations, no shared state changes — pure container swap

---

## Files touched in this rollout

- `cli/headless.py` (new)
- `providers/_shared.py` (added `renderer=` parameter, console=None guard)
- `providers/{azure_openai,nvidia,openrouter,ollama}.py` (collapsed into BaseOpenAIProvider subclasses)
- `cli/renderer.py` (segmented text state, throttled markdown updates)
- `deploy/ide/Dockerfile.code-server` (multi-stage, installs VSIX as coder user)
- `deploy/ide/extensions/crowe-logic/**` (new VS Code extension)
- `deploy/ide/product-overrides.json` (new)
- `deploy/ide/scripts/apply-product-overrides.sh` (new)
- `deploy/ide/scripts/smoke-test.sh` (new)
- `deploy/ide/settings.json` (Crowe Logic Dark theme + croweLogic.* defaults)
- `deploy/ide/extensions.txt` (dropped Dracula, noted local VSIX)
