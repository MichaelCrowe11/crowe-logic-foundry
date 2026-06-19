# Tello Drone Integration — Design

**Date:** 2026-06-19
**Status:** Design (pending implementation plan)
**Module:** `crowe-logic-foundry/tools/tello.py`

## Purpose

Give the `crowe-logic` agent a controllable aerial sensor: it can fly a drone,
capture frames, and hand those frames to perception tooling that already exists
(`tools/vision.py` → mycelium-ei / Crowe Vision) and to the mycology backend
(`crowe-farm-automation`). The drone is a **new sensor, not a new brain** — all
intelligence lives in tools the agent already owns; this module only adds flight
and frame capture.

This single foundation serves every intended mission by letting the agent
*compose* primitives rather than baking one mission into code:

- Indoor grow/lab scouting (capture trays/blocks → contamination assessment)
- Outdoor site survey (grid of frames → health/mapping analysis)
- Agent-controlled R&D / demo platform (the general capability)
- Content / cinematics capture (frames/footage → video tooling)

## Hardware decision (recorded)

- **Target hardware: DJI Tello EDU** (~$130). It is the standard *programmable*
  drone: official SDK, Python wrapper (`djitellopy`), real frame access.
- **Rejected: generic "HiDRONE PRO" / LF620-class toy drones** (offered at $400;
  real street price ~$30–60; fake $799 MSRP). These are closed black boxes — no
  SDK, no API, no usable feed — so they cannot integrate with `crowe-logic` or
  `mycelium-ei` at all. Do not buy for this purpose.
- Outdoor survey is the weakest fit for Tello hardware (indoor-class, no GPS).
  It is supported by the same tool surface, but a GPS/SDK-capable craft
  (DJI Mini/Air + Mobile SDK, or a PX4/ArduPilot build with a companion
  computer) is the right hardware when outdoor survey becomes primary. The
  software design below does not change — only the flight backend would.

## Architecture (Approach A: thin sensor + reuse)

```
agent
  │  tello_arm() ─ required before any flight
  ├─ tello_takeoff / tello_move / tello_rotate / tello_land
  ├─ tello_capture(label) ──► frame.jpg on disk
  │                              │
  │                              ▼
  │                        analyze_image(path, prompt)   [EXISTING tools/vision.py]
  │                              │  → mycelium-ei / Crowe Vision verdict
  │                              ▼
  └─ tello_log_observation(...) ──► crowe-farm-automation API   [optional, graceful]
                                     (ContaminationReport / grow observation)
```

The drone never talks to the analysis stack directly. The agent orchestrates:
capture → analyze (existing tool) → report (optional). This is what makes all
four missions emerge from one module — the agent chains the primitives
differently per mission.

### Why not the alternatives
- **Monolithic mission tools** (`scout_grow_room()` that flies+captures+analyzes
  in one call): convenient for one mission, rigid for the others, and bakes
  policy into code. Rejected.
- **Separate drone microservice** (FastAPI the agent calls over HTTP): overkill
  for one drone on local WiFi; adds a process and a failure mode. Rejected.

## Tool surface

All tools are plain functions returning JSON strings, with `:param:`/`:return:`
docstrings so `registry.py` auto-generates the function-calling schema (same
pattern as every other module in `tools/`).

**Connection & state**
- `tello_connect()` — connect to the drone's WiFi SDK, start the video stream.
- `tello_status()` — battery %, height, temperature, flight/armed state, link.
- `tello_disconnect()` — stop stream, release the connection.

**Flight primitives** (all no-op with a clear error unless armed, except e-stop/land)
- `tello_arm()` — explicit gate; must be called before any movement.
- `tello_disarm()` — revoke flight authorization (does not land a flying drone;
  use `tello_land`).
- `tello_takeoff()`
- `tello_land()` — always available, even when disarmed.
- `tello_move(direction, cm)` — direction ∈ {forward, back, left, right, up, down}.
- `tello_rotate(degrees)` — signed; clockwise positive.
- `tello_emergency()` — cut motors immediately; always available.

**Perception**
- `tello_capture(label)` — grab the current video frame, save to a session
  capture dir, return `{ "path": ..., "label": ..., "ts": ... }`. The path feeds
  directly into the existing `analyze_image(path, prompt)`.

**Report bridge (optional)**
- `tello_log_observation(path, kind, notes)` — POST a captured (optionally
  analyzed) frame to `crowe-farm-automation` as a `ContaminationReport` / grow
  observation. Degrades gracefully (returns a clear "farm API unreachable"
  result) when the backend is not running.

## Safety model (chosen: armed + caps + e-land)

Flying a physical drone on LLM-issued commands indoors is the primary risk. The
module enforces:

1. **Arming gate** — `tello_arm()` is required before `takeoff`/`move`/`rotate`.
   A fresh connection is disarmed. Movement tools called while disarmed return an
   error JSON instructing the agent to arm first; they do not fly.
2. **Movement caps** — per-command distance cap (default **100 cm**), rotation
   cap (default **180°**), and a max-altitude cap. Requests beyond caps are
   clamped-with-warning or rejected (decide in plan; default = reject with a
   clear message so the agent re-issues a smaller move).
3. **Always-available stop** — `tello_emergency()` (motors off) and `tello_land()`
   work regardless of armed state.
4. **Auto-land guards** — auto-land (or e-stop) on **low battery** (default ≤15%)
   and on **lost link / command timeout**. Status checks surface these before
   they trigger.

All caps/thresholds are module constants, overridable via env (e.g.
`TELLO_MAX_STEP_CM`, `TELLO_MIN_BATTERY_PCT`) so they are tunable without code
edits — mirroring how other tools read config from env.

## Registration & dependencies

- `djitellopy` (+ its `av`/`opencv` deps for frame grab) added as an **optional**
  dependency group (e.g. `[drone]`), so the base install is unaffected.
- Tools register **conditionally**: only when `djitellopy` imports successfully.
  Connection-dependent tools surface a clear "no drone connected" result rather
  than raising, mirroring `crowe_terminal`'s runtime-gated registration.
- A short `system_prompt()` addendum tells the agent the safety contract:
  *arm before flying, one bounded move at a time, check status between moves,
  emergency-land if anything is uncertain.*

## Error handling

- Every tool returns JSON; no exceptions escape to the agent loop.
- Connection failures, disarmed-movement attempts, cap violations, low battery,
  and unreachable farm API each return a distinct, actionable `error` field.
- Frame capture failure (no stream) returns an error, never a stale/empty file.

## Testing

Hardware-in-the-loop is not assumed in CI. The module is split so logic is
testable without a drone:

- **Pure logic, unit-tested (no hardware):** arming state machine, cap
  clamping/rejection, battery/link guard decisions, JSON shapes, and the
  `tello_log_observation` payload builder. Drive these against a **fake drone
  backend** (a stub implementing the `djitellopy.Tello` methods used).
- **Capture path:** mock the frame source; assert a file is written and the
  returned path is valid; assert no file on capture failure.
- **Integration (manual, documented):** a `docs`/runbook checklist for a real
  Tello EDU — connect, arm, takeoff, one capture → `analyze_image`, land —
  run by a human in a clear space. Not in CI.

## Out of scope (YAGNI)

- Autonomous waypoint/mission planning, SLAM, multi-drone, obstacle avoidance.
- Outdoor/GPS flight backend (design accommodates it; not built now).
- A drone UI in Cortex (the agent + CLI are the interface for v1).

## Prerequisite

Acquire a **DJI Tello EDU** (or Tello). No flight code can be verified end-to-end
until hardware is on hand; all non-hardware logic above is built and unit-tested
in the meantime behind the fake backend.
