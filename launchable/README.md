# Crowe Talon on NemoClaw: Brev Launchable

A one-click Brev Launchable that provisions the NVIDIA NemoClaw stack
(NIM inference plus OpenShell sandbox) and connects it to the Crowe
Logic Foundry CLI on your laptop.

## What you get

| Component | Runs | Purpose |
|---|---|---|
| NIM inference | Brev VM, port 8000 | Hosts the Talon model (Nemotron by default, override via `NIM_IMAGE`). |
| OpenShell sandbox | Brev VM, port 8001 | Executes shell commands inside an isolation boundary. Prevents the agent from touching your laptop's filesystem. |
| Foundry CLI | Your laptop | Sends turns to NIM and tool calls to OpenShell. Nothing agent-runtime lives on the VM. |

Only the two server components run on the VM. The Foundry CLI stays on
your operator machine, which is the same operational model Crowe Logic
uses for every other agent in the chain.

## Launch

1. Click the Launchable URL in the Crowe Logic dashboard, or import
   `launchable/brev.yaml` as a new Launchable in the Brev console.
2. Pick an L40S (or larger) instance. The default `brev.yaml` reserves
   200 GB disk for NIM model weights plus room to grow.
3. Wait for the provision to finish. Brev will tail
   `/var/log/crowe-talon-bootstrap.log` automatically.
4. Copy the `.env` block from the bootstrap output into
   `~/.config/crowe-logic/.env` on your laptop.
5. In Foundry CLI:
   ```
   /model resolve talon-nemoclaw
   /model talon-nemoclaw
   ```

## Files

- `brev.yaml`: Launchable manifest Brev reads on import.
- `bootstrap.sh`: First-boot script. Clones Foundry, starts NIM, starts
  OpenShell, runs recon, prints the operator `.env` block. Idempotent.
- `../scripts/nemoclaw_recon.sh`: Read-only probe that discovers the
  actual port and path NemoClaw exposes, since alpha builds have been
  moving. Emits the final `.env` snippet.
- `../agents/crowe-talon.yaml`: Agent profile the Launchable targets.
  Loadable from the CLI via `crowe-logic launch crowe-talon`.
- `../tools/nemoclaw.py`: `nemoclaw_shell` and `nemoclaw_health`
  registered into the tool registry so Talon can call them like any
  other tool.

## Cost notes

NemoClaw VMs are not cheap. A single L40S on Brev runs in the low-dollars
per hour range. Shut down the VM when you are not actively using Talon,
or use Brev's scheduled stop to cap daily burn. Foundry will surface a
connection error when the VM is paused, which is the right failure mode:
you see clearly that the backend is off, and `/model talon-nemoclaw`
turns simply fail until you restart the VM.

## Troubleshooting

See `docs/nemoclaw-integration.md` in the repo root for the full
troubleshooting guide, including the common 404-on-exec-path case and
the workaround.
