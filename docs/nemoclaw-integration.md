# NemoClaw Integration

Crowe Talon runs on NVIDIA NemoClaw, a single-VM reference stack that pairs
NIM inference with the OpenShell sandbox runtime. Inference and tool execution
share one Brev-provisioned host, so shell calls reach the model through a
loopback hop instead of a public internet round-trip.

## Architecture

```
  Operator laptop                     Brev VM (NemoClaw)
  ┌────────────────┐   openai_compat   ┌─────────────────────┐
  │ crowe-logic    │ ─────────────────>│ NIM inference       │
  │ Foundry CLI    │   (NEMOCLAW_      │ (llama-3.1-nemotron │
  │                │    ENDPOINT)      │  or recon-detected) │
  │  tools/        │                   │                     │
  │  nemoclaw.py   │   HTTP exec       │ OpenShell sandbox   │
  │                │ ─────────────────>│ (NEMOCLAW_SANDBOX_  │
  └────────────────┘   (NEMOCLAW_      │  EXEC_PATH)         │
                       SANDBOX_URL)    └─────────────────────┘
```

Inference rides on the standard `openai_compat` provider, so there is no
Foundry-side glue beyond the `crowelm-talon-nemoclaw` entry in
`config/models.extra.json`. The sandbox side is owned by `tools/nemoclaw.py`,
which exposes `nemoclaw_shell` and `nemoclaw_health` to the agent registry.

## Environment contract

| Variable | Purpose | Default |
|---|---|---|
| `NEMOCLAW_ENDPOINT` | Base URL of the VM. Used for both inference and, unless overridden, the sandbox. | (required) |
| `NEMOCLAW_API_KEY` | Bearer token or Brev access token. | (required) |
| `NEMOCLAW_MODEL_NAME` | Overrides `${NEMOCLAW_MODEL_NAME}` interpolation in `config/models.extra.json`. | Value in the JSON entry. |
| `NEMOCLAW_SANDBOX_URL` | Separate base URL for OpenShell when it runs on a different host or port. | Falls back to `NEMOCLAW_ENDPOINT`. |
| `NEMOCLAW_SANDBOX_EXEC_PATH` | POST target for shell execution. | `/openshell/v1/exec` |
| `NEMOCLAW_SANDBOX_HEALTH_PATH` | GET target for liveness. | `/openshell/v1/health` |
| `NEMOCLAW_SANDBOX_TIMEOUT` | HTTP-level timeout floor in seconds. | `180` |

The interpolation in `provider_model_name()` (see `config/agent_config.py`)
resolves `${NEMOCLAW_MODEL_NAME}` at request time, so a single JSON entry
works across VMs whose NIM deployments ship different model ids.

## First-time setup

1. Provision the VM in Brev. Any NemoClaw-compatible image works; the
   NVIDIA Agent Toolkit reference image is tested.
2. `brev shell <vm-name>`. Inside the VM, run the recon script:
   ```
   bash scripts/nemoclaw_recon.sh
   ```
   It probes the live ports and paths for NIM and OpenShell, prints a
   ready-to-paste `.env` block, and reports the actual model id served
   by the inference endpoint.
3. Paste the generated block into `~/.config/crowe-logic/.env` on your
   laptop. At minimum you need `NEMOCLAW_ENDPOINT`, `NEMOCLAW_API_KEY`,
   and `NEMOCLAW_MODEL_NAME`.
4. Verify from the CLI:
   ```
   /model resolve talon-nemoclaw
   ```
   Confirm the provider is `openai_compat` and the backend name matches
   what the recon script printed.
5. Run a health check:
   ```
   /tools
   ```
   then issue a prompt asking the agent to call `nemoclaw_health`. A
   `reachable: true` response means OpenShell is answering on the
   configured path.

## Launching the Talon agent

```
/model talon-nemoclaw
```

or invoke the full agent profile (tools + prompt override from
`agents/crowe-talon.yaml`):

```
crowe-logic launch crowe-talon
```

Talon's prompt steers hard toward concise plan-then-execute behavior and
treats `nemoclaw_shell` as the default shell so the agent does not
accidentally fall back to the operator's local `execute_shell`.

## Dual-mode pairing

Talon pairs cleanly with CroweLM Supreme for comparative testing:

```
/dual supreme talon-nemoclaw
```

The preflight probe in `cli/dual_mode.py` checks the Ollama Cloud models
but treats `openai_compat` endpoints as opaque (their auth errors already
surface at provider construction), so a misconfigured NemoClaw URL will
not trigger the fallback chain. It will surface the provider's connection
error at turn time, which is the right behavior for debugging an integration.

## Troubleshooting

**`nemoclaw_shell` returns 404 on every call.** NemoClaw alpha builds have
served OpenShell at several different paths. Re-run the recon script on the
VM, note the `[hit] exec` line, and set `NEMOCLAW_SANDBOX_EXEC_PATH` to
match.

**Model switch succeeds but all turns return "connection refused".** The
NIM server is not bound to the address in `NEMOCLAW_ENDPOINT`. SSH into the
VM and check `ss -tlnp | grep LISTEN`. If NIM is on a private port, add a
Brev port share and update `NEMOCLAW_ENDPOINT` to the public share URL.

**`nemoclaw_health` says `httpx not installed`.** The Foundry install is
missing httpx. Run `pip install -r requirements.txt` in the foundry repo.

**Tool calls complete but return garbage output fields.** The NemoClaw VM
is running an older OpenShell schema that uses `output`/`exit_code` rather
than `stdout`/`return_code`. `tools/nemoclaw.py` already handles both
conventions, but if the response is missing both pairs the issue is upstream
of Foundry.
