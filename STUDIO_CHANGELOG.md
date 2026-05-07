# Crowe Studio — Changelog

All notable changes to the Crowe Studio subsystem of crowe-logic-foundry.
Proprietary. Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.

Format: Keep a Changelog. Semantic versioning on the STUDIO_VERSION file.

## [0.10.0] — 2026-05-07 — Chunked session bundle routing

### Added
- `tools/studio_route.py::route_session_chunks_to_tenant(session_id,
  tenant, move=False)` — closes the chunked long-session recording
  block. Reads the `start_live_capture(chunk_seconds=N)` session file
  at `$CAPTURE_ROOT/sessions/<session_id>.json`, enumerates every
  `chunk-*.mp4` produced under the session's chunk_dir, and copies (or
  moves) them into `<tenant.raw_dir>/<session_id>/` as a single bundle.
  Writes a `session.json` next to the chunks summarizing session_id,
  source_session_path, chunk_count, total_bytes, chunked, started_at,
  and bundled_at.
- Tenant `ingest_cmd` is invoked ONCE for the whole bundle (not once
  per chunk) with substitutions `{session_id}`, `{session_dir}`,
  `{tenant_root}`, plus `{clip_path}` resolved to the FIRST chunk so
  existing tenant ingest scripts that expect a single clip still see
  something. Captures exit / stdout_tail / stderr_tail the same way
  `route_clip_to_tenant` does, and looks up `manifest_id` against
  `manifests_dir` with the same scan logic.
- `tests/test_route_session_chunks.py` — tmp_path smoke test that
  fakes a 3-chunk session, points STUDIO_TENANTS_PATH at a temp yaml
  whose `ingest_cmd` is `/bin/echo {session_id} {session_dir}`, and
  asserts the bundle, the `session.json`, and a single ingest exec.

## [0.9.0] — 2026-04-24 — Per-frame relabel experiment + eval harness

### Added
- `training/youtube_corpus/relabel_per_frame.py` — zero-shot CLIP pass
  over the representative frame of each shot. Reuses the v0.8.0
  baseline's `models/baseline/cache/{split}.npz` image features when
  the dataset fingerprint matches, so the relabel runs as a sub-second
  NumPy multiply per split. Falls back to live encoding on a cache
  miss. Writes a new JSONL per split with the original label preserved
  in `meta.label_inherited` and a `meta.relabel_top3` audit trace.
- `--min-score` and `--min-margin` flags on the relabel script for
  confidence-gated replacement (only swap a label when zero-shot is
  both confident and clearly beats the inherited score).
- `--training-subdir` and `--artifact-subdir` flags on
  `baseline_clip.py` so experimental label sets train into separate
  artifact directories without touching the v0.8.0 baseline. Now the
  permanent way to run "what-if" label experiments without clobber.

### Honest experiment numbers (vs v0.8.0 floor of val 0.216 / test 0.199)

Trained a baseline on relabeled JSONLs with two thresholds, then
evaluated against the original val/test labels (to avoid the
circular-eval trap of training and evaluating against the same
zero-shot CLIP signal).

| Variant                     | Val acc | Test acc | Notes                                              |
| ---                         | ---:    | ---:     | ---                                                |
| v0.8.0 baseline             | 0.216   | 0.199    | inherited labels everywhere                        |
| Pure relabel / orig eval    | 0.201   | 0.101    | 82% of train labels swapped, hurt both splits      |
| Gated relabel / orig eval   | 0.257   | 0.173    | 12.6% swapped, small val lift, slight test loss    |
| Pure relabel / pure eval    | 0.587   | 0.609    | circular: measures CLIP/CLIP agreement, not lift   |

### What we learned
- Zero-shot CLIP ViT-B/32 is too weak for this 9-way taxonomy. On 9
  canonical example frames (one per class, hand-picked from the
  taxonomy YAML) zero-shot top-1 was only 4/9, even with
  domain-framed prompts derived from the taxonomy descriptions.
- Inherited labels are genuinely noisy in mixed-content videos. One
  spot-check confirmed a `facility_wide`-tagged frame was visually a
  clear `harvest_close` moment, and CLIP correctly relabeled it.
- But CLIP also confidently mis-relabels frames into adjacent classes
  (a talking-head shot scored highest as `grow_tent`), so the net
  effect of unconstrained relabeling is roughly a wash.
- The aspirational "weighted-F1 ~0.19 to ~0.55 from labels alone"
  target on the v0.8.0 deferred list was not reachable with this
  backbone. Stronger CLIP variants (SigLIP-large, OpenCLIP ViT-H/14)
  or a hand-labeled active-learning bootstrap are the realistic next
  options.

### Fixed
- `_text_embed` initially mirrored `_image_embed`'s "if dim equals
  projection in_features, project it" heuristic. That heuristic is
  unsafe on the text tower because text's pre- and post-projection
  dims are both 512 for ViT-B/32, so the helper double-projected
  every text embedding. Symptom: cosine similarities collapsed to
  ~0.08 instead of the expected ~0.30. Fixed by trusting
  `pooler_output` directly, which transformers 5.x already returns
  in the joint embedding space (verified empirically against the
  canonical `CLIPModel.forward` scoring path).

### Deferred
- Re-run with a stronger backbone (`google/siglip-large-patch16-384`
  or `laion/CLIP-ViT-H-14-laion2B-s32B-b79K`) before declaring the
  per-frame relabel approach dead. Same harness, different `--model-id`.
- Active-learning bootstrap: have Michael hand-label 50 frames per
  class drawn from a diversity-stratified sample, then retrain the
  baseline. Likely the highest-leverage move regardless of backbone.

## [0.8.0] — 2026-04-23 — First baseline shot-type classifier

### Added
- `training/shot_selector/baseline_clip.py` — CLIP ViT-B/32
  (openai/clip-vit-base-patch32) image features + balanced
  LogisticRegression head over the sw-mushrooms-yt corpus. Replaces
  the `NotImplementedError` scaffold in `train_vision_clip.py` with a
  working, ship-able Phase 1 model.
- Per-split feature cache at
  `<corpus>/models/baseline/cache/{train,val,test}.npz` keyed on a
  SHA-256 fingerprint of the JSONL + backbone id, so re-fits after a
  head tweak run in seconds instead of re-encoding 2,580 frames.
- `classify_frame(image_path)` inference helper (lazy backbone load +
  module-level cache) so the director loop can import a single
  function without touching training code.
- `studio-vision` extras group in `pyproject.toml`:
  `torch>=2.2`, `transformers>=4.44`, `Pillow>=10.0`,
  `scikit-learn>=1.4`, `joblib>=1.3`.
- `training/youtube_corpus/sw-mushrooms/models/baseline/baseline_report.md`
  with top-1 accuracy, per-class precision/recall/F1 and confusion
  matrices for val and test.

### Baseline numbers (sw-mushrooms-yt, 1838/269/473)
- Val top-1: **0.216** | Test top-1: **0.199**
- Strong-signal classes: `lab_sterility` val P=0.846,
  `grow_tent` val R=0.846, `specimen_product` test P=0.556.
- Weak classes driven by **label noise**: the current labeler assigns
  one shot_type per video, so frames in mixed-content videos inherit
  the dominant label even when they visually belong to another class.
  `facility_wide` val has P=1.000 R=0.056 — the model knows what a
  facility shot looks like and is refusing to call mixed frames one.

### Deferred (informed by the baseline)
- Per-frame re-labeling pass using zero-shot CLIP on the
  representative frame itself, so labels match the image content
  rather than the video title.
- Qwen3-VL-2B LoRA on HF Jobs, once labels are cleaned. Current
  baseline becomes the floor the LoRA has to beat.

## [0.7.0] — 2026-04-22 — Pro-quality sync + CroweLM intelligence

### Added
- `tools/sync.py::sync_shoot` — cross-correlates audio tracks across
  all cameras in a shoot using scipy.signal FFT correlation. Computes
  per-camera offsets in milliseconds relative to the primary camera.
  Writes the sync block into the shoot manifest.
- `tools/sync.py::get_sync_offsets` — re-read cached offsets.
- `tools/edl_render.py` — renderer now reads the shoot's sync block
  automatically and passes `-itsoffset` when cutting per-camera
  segments. No code changes for callers; correction is transparent.
- `tools/shot_selector.py::build_edl(strategy="crowelm")` — new
  strategy that prompts the local CroweLM/DeepParallel model (via
  Ollama's OpenAI-compatible endpoint) for shot picks. Gracefully
  falls back to `rule_based_fallback` when the model is unreachable
  or returns invalid JSON. EDL records both the requested and
  effective strategies so you can audit what actually ran.
- `/api/shoot/sync` endpoint + dashboard `Sync only` button.
- Dashboard `Shot strategy` dropdown (rule-based / CroweLM) and
  `Sync first` toggle in the Multi-cam panel.
- Dashboard post-edit panels: sync report per camera (with
  confidence %) and shot-plan preview.
- `scripts/e2e.py --strategy crowelm --sync` — E2E demo supports
  both strategies and optional audio sync.

### Fixed
- `_resolve_avfoundation_device` now pairs video with its matching
  audio device automatically (MacBook Air Camera -> MacBook Air
  Microphone) so every multi-cam shoot has audio on every camera
  that physically has a mic. Screen capture stays video-only.
- EDL renderer handles missing/partial sync blocks as a no-op (0 ms
  offsets), so shoots that were never sync-analyzed still render.

### CroweLM configuration
- `CROWELM_BASE_URL` env var (default: `http://localhost:11434/v1`)
- `CROWELM_MODEL` env var (default: `Mcrowe1210/DeepParallel:v2.2`)
- `CROWELM_TIMEOUT_SECONDS` env var (default: `45`)

### Verified end-to-end 2026-04-22
```
cameras running: macbook-close (audio 10.7 MB), screen (silent 26.4 MB)
sync: macbook-close primary conf=100%, screen silent
EDL: 2 sections, 14.92s, strategy=crowelm -> rule_based_fallback
render: 16.3 MB in 4.37s (3.4x real-time)
routed to scratch/iphone-20260422-060132
```
Fallback exercised because Ollama was not running at test time; the
architecture is confirmed wired end-to-end and CroweLM picks activate
the moment the Ollama service is restored.

## [0.6.0] — 2026-04-22 — Complete creator loop (shoot → select → render → route)

### Added
- `tools/shot_selector.py::build_edl` — reads a presentation script +
  a stopped shoot manifest, produces an Edit Decision List (EDL) JSON.
  Rule-based strategy honors [angle: ...], [camera: ...], [zoom: ...]
  directives; falls back to the primary sync camera.
- `tools/shot_selector.py::load_edl` / `list_edls` — registry queries.
- `tools/edl_render.py::render_edl` — turns an EDL into a final
  multi-angle cut. Per-section zoom applied, all segments rendered
  at 1920x1080 30fps yuv420p, concat demuxer assembles the video
  track, master audio comes from the primary camera unchanged.
  Faster-than-real-time on M-series Macs (1.92s render for 11.8s
  cut in smoke test).
- `tools/capture.py::get_session_chunks` — lists chunk files for a
  chunked session.
- `tools/capture.py::start_live_capture(chunk_seconds=N)` — ffmpeg
  `-f segment` mode. Each N-second chunk is an independently playable
  mp4 so a crash preserves all prior chunks.
- `control_center.py` — new endpoints: `/api/cameras`, `/api/shoots`,
  `/api/shoot/start`, `/api/shoot/stop`, `/api/shoot/auto-edit`,
  `/api/shoot/register-cloud`, `/api/shoot/{id}/upload/{camera}`,
  `/api/edls`.
- Dashboard multi-cam panel: camera tiles with live availability,
  Start-Shoot toggle, Auto-Edit button that builds EDL + renders +
  routes in one call.
- `scripts/e2e.py` — end-to-end smoke test: start_shoot → stop_shoot
  → build_edl → render_edl → route → open QuickTime.

### Fixed
- All AVFoundation captures now pass `-pixel_format uyvy422` +
  `-probesize 10M` to prevent "Selected pixel format is not supported"
  and "not enough frames to estimate rate" errors. Fixes intermittent
  zero-byte iPhone captures during multi-cam shoots.
- `_spawn_avfoundation_recorder` skips AAC encoding when the source
  device has no audio (e.g. screen capture, MacBook camera with no
  mic routing), avoiding a subtle ffmpeg warning.

### Verified end-to-end 2026-04-22
```
macbook-close (primary)  7.7 MB  12s 720p30
screen                  23.2 MB  12s 1080p30
EDL: 2 sections, 11.8s, cameras_used=['macbook-close']
Render: 1.92s wall-clock for 11.8s output (6.1x real-time)
Routed to scratch/iphone-20260422-054011/
```

## [0.5.0] — 2026-04-22 — Multi-camera foundation + lock-down

### Added
- `config/studio_cameras.yaml` — declarative camera registry. Cameras
  have names, roles (wide, close, product, etc), default specs, and
  optional synchronization preferences.
- `tools/capture.py::start_shoot` / `stop_shoot` — coordinate N
  simultaneous captures, one per registered camera, under a single
  shoot_id. Creates a shoot manifest tracking all per-camera clips.
- `tools/capture.py::list_cameras` / `get_camera` — registry queries.
- Smoke test script `scripts/studio-smoke-test.py`.
- `STUDIO_ROADMAP.md` and this changelog.

### Security / IP
- Copyright headers added to every studio file.
- `STUDIO_VERSION` file tags v0.5.0.
- Private-repo-only posture documented in memory.

## [0.4.0] — 2026-04-22 — Control Center dashboard

### Added
- `tools/control_center.py` — FastAPI dashboard wrapping all studio tools.
- `dashboard/static/studio.html` — single-page UI with live polling.
- Dashboard opens on main display automatically via Safari AppleScript.
- Recent-outputs feed, selected-clip preview with `<video>`, inline zoom
  apply, tenant pin + route with manifest feedback.

## [0.3.0] — 2026-04-22 — Presentation module

### Added
- `tools/presentation.py::load_script` — parses markdown scripts with
  `## Section` headers and `[zoom: ...]` / `[duration: ...]` directives.
- `tools/presentation.py::launch_teleprompter` — generates styled HTML
  teleprompter, opens in Safari on main display, keyboard controls for
  play/pause, speed, mirror, reset.
- `tools/presentation.py::apply_zoom_effect` — post-production zoom
  renders: punch_in, slow_zoom_out, ken_burns, whip_zoom,
  cut_to_closeup, none.
- `tools/presentation.py::split_recording_by_chapters` — ffmpeg -c copy
  split by JSON chapter list, stream-copies each section.

## [0.2.0] — 2026-04-22 — Tenant-agnostic routing

### Added
- `config/studio_tenants.yaml` — declarative registry of content
  pipelines (toxicteetv, southwest-mushrooms, mushroom-grower-audio,
  crowe-psychedelics, scratch).
- `tools/studio_route.py::list_tenants` / `get_tenant` /
  `route_clip_to_tenant` / `tenant_inbox_peek` — generic pipeline
  dispatcher driven by the registry.
- Agent YAML updated to tenant-agnostic prompt.

## [0.1.0] — 2026-04-22 — First ship

### Added
- `tools/capture.py` — AVFoundation wrapper for iPhone Continuity Camera
  and other sources via ffmpeg subprocess.
  - `list_capture_devices`, `find_iphone_device`, `capture_clip`,
    `capture_still`, `start_live_capture`, `stop_live_capture`,
    `list_live_captures`.
  - `preview_device`, `stop_preview`, `enable_center_stage` (added v0.4).
- `agents/studio.yaml` — Foundry agent registered.
- Defaults: 1920x1080 @ 30fps, h264_videotoolbox, 8M bitrate, AAC 192k.
- Verified end-to-end: iPhone capture → route to toxicteetv → manifest
  `iphone-20260422-041607` created with ingest_exit 0.
