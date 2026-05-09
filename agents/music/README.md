# CroweLM-Music sub-cluster

The agent team that builds and runs Crowe Talon Pro.

## What this is

Ten specialist agents inside Crowe Logic Foundry, scoped to music engineering.
Together they replace what would otherwise be a $700k-$1M/yr engineering team:
composition, mix, master, DSP, native UI, web UI, provenance, review, test,
orchestration. Sole human gates: listening review, partner-facing trust,
product taste calls, real-time crash debugging.

## Roster

| Agent | Tier | Model | Owns |
|---|---|---|---|
| music-orchestrator | premium | Talon | routing, state, landing gates |
| music-compose | premium | Talon | composition + arrangement (.synapse source) |
| music-provenance | premium | Talon | watermark spec, attestation, copyright posture |
| music-mix | research | Talon Super | mixing engineer (mix block of .synapse) |
| music-master | engineering | Coder | mastering pipeline driver, parameter choices |
| music-dsp | engineering | Coder | audio engine code (@talon/master, AVAudioEngine bridge, JUCE wrapper) |
| music-native | engineering | Coder | Swift Mac app surface |
| music-web | engineering | Coder | @crowe/workstation chrome + Talon Pro UI |
| music-critic | workhorse | Eclipse (Ollama Pro) | reviews every output before landing |
| music-test | workhorse | Eclipse (Ollama Pro) | test authoring + regression rigs |

## Tier economics

| Tier | Model | Provider | Use case | Cost shape |
|---|---|---|---|---|
| premium | crowelm-talon | NVIDIA NIM | user-facing taste, routing | high/call, low rate |
| research | crowelm-talon-super | NVIDIA NIM | mix iteration | medium/call, medium rate |
| engineering | crowelm-coder | NVIDIA NIM (qwen3-coder) | code generation | medium/call, medium rate |
| workhorse | crowelm-eclipse | Ollama Pro (kimi-k2.6:cloud) | review, test, internal | near-zero/call, high rate |

The workhorse tier is what makes the cluster economically viable. Critic fires
on every diff; test rigs run continuously. Premium-tier per-call cost would
make that prohibitive. With Ollama Pro hosting kimi-k2.6:cloud at the $100/mo
tier, the marginal cost of an internal review or test run rounds to zero.

## How a request flows

```
operator (Talon Pro AI panel)
    │
    ▼
music-orchestrator           [premium tier]
    │
    ├── routes to specialist(s) based on intent
    │
    ▼
specialist                   [research/engineering tier]
    │
    ▼
music-critic                 [workhorse tier, fires on every output]
    │
    ├── PASS  → orchestrator commits (critic green + test green)
    └── BLOCK → orchestrator routes back to specialist with finding
    │
    ▼
music-test                   [workhorse tier, runs rigs]
    │
    ▼
landing on main
```

## Coordination rules

- **Fanout is orchestrator-only.** Specialists do not invoke each other.
  Orchestrator dispatches and gathers. Keeps state consistent, makes
  parallelism safe.
- **Critic runs on every specialist output.** No exceptions.
- **Landing gate**: critic must return `PASS` and test must be green for
  the cluster to commit on main.
- **External state changes**: orchestrator can commit (with critic pass);
  push and deploy require explicit operator approval.

## Style rules (enforced by music-critic)

- No em dashes anywhere in generated output.
- No emojis.
- No "AI access" or "AI tier" framing in pricing/marketing copy.
- No client tech-stack exposure in client-facing docs.
- Effective dates on legal pages must match the date content actually changed.

## Spinning up the cluster

The orchestrator is the single entry point. The Talon Pro AI panel sends
prompts to `music-orchestrator`. The orchestrator handles routing, brief
construction, and gathering. Specialists are stateless across requests; the
orchestrator owns project state.

To launch the cluster from the Foundry CLI:

```sh
crowe-foundry cluster up crowelm-music
```

Status:

```sh
crowe-foundry cluster status crowelm-music
```

Single-agent invocation (debugging only; production traffic goes through
the orchestrator):

```sh
crowe-foundry agent run music-compose --prompt "Sketch a bridge for Velvet Algorithm in Dm pulled toward Bb"
```

## Connection to Crowe Talon Pro Mac app

The Talon Pro Mac app's AI panel is the user-facing surface for this cluster.
Every prompt typed into the panel goes to `music-orchestrator`. The
orchestrator reads the current `.synapse` project state from the Mac app's
local HTTP bridge, routes to specialists, gathers their structured edits,
runs critic + test, and writes the result back through the bridge. The Mac
app re-renders the timeline. The signing badge on the project bar updates
when master state changes.

This is the architecture that makes Talon Pro "Crowe Terminal but for music":
the same agent-cluster + workstation-chrome model, specialized for a
different domain.

## Status

| Phase | Status | Notes |
|---|---|---|
| Cluster definitions (this directory) | drafted 2026-05-09 | yaml specs ready; needs Foundry registry registration |
| Routing rules (orchestrator) | drafted | implementation in `crowe_synapse_engine` pending |
| Tool wiring | partial | talon_* tools exist; substrate_* tools exist; needs music-specific tool additions |
| AI panel integration in Talon Pro Mac app | not yet | depends on @crowe/workstation library |
| Production readiness | no | needs first end-to-end run + listening gate |

## Owner

Michael Crowe, Crowe Logic, Inc. Phoenix, AZ.
