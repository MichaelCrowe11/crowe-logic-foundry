# Crowe Studio — Roadmap

Proprietary. Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.

Three lanes: **Now**, **Soon**, **Someday**. Only Michael moves items
across lanes. Every shipped feature lands in STUDIO_CHANGELOG.md with a
bumped STUDIO_VERSION.

---

## Now — this week

### Multi-camera foundation
- [x] Camera registry YAML
- [x] `start_shoot` / `stop_shoot` multi-source capture
- [x] Shoot manifest bundling N clips per session
- [x] Dashboard panel showing active shoot with per-camera tiles
- [ ] Audio-sync alignment using waveform correlation (solves "start
      every camera within 100ms of each other")

### Lock-down
- [x] Copyright headers on every file
- [x] STUDIO_CHANGELOG.md
- [x] STUDIO_ROADMAP.md
- [x] Smoke-test script
- [x] v0.5.0 tag
- [x] v0.6.0 tag
- [ ] Trademark search for "Crowe Studio" / "Studio Foundry"

### Chunked long-session recording
- [x] `start_live_capture(chunk_seconds=N)` uses ffmpeg `-f segment`
- [x] `get_session_chunks(session_id)` returns all produced segments.
- [ ] `route_session_chunks_to_tenant(session_id, tenant)` bundle route.

### Creator loop (shipped v0.6.0)
- [x] `shot_selector.build_edl` — script + shoot → EDL (rule-based)
- [x] `edl_render.render_edl` — EDL → final multi-angle cut
- [x] `/api/shoot/auto-edit` — one-call shoot-to-final
- [x] Dashboard Auto-Edit button
- [x] End-to-end smoke test verified

### Pro-quality sync + CroweLM intelligence (shipped v0.7.0)
- [x] `sync.sync_shoot` — waveform cross-correlation via scipy
- [x] Renderer applies per-camera sync offsets via `-itsoffset`
- [x] `build_edl(strategy="crowelm")` — DeepParallel via Ollama
- [x] Fail-soft fallback to rule-based when Ollama unreachable
- [x] Dashboard strategy dropdown + sync panel
- [x] `/api/shoot/sync` endpoint for manual sync trigger
- [x] v0.7.0 tag

### Baseline shot-type classifier (shipped v0.8.0)
- [x] `training/shot_selector/baseline_clip.py` — CLIP ViT-B/32 +
      balanced LogisticRegression over the 2580-pair corpus
- [x] Per-split feature cache keyed on dataset fingerprint
- [x] `classify_frame(image_path)` inference helper for downstream use
- [x] `studio-vision` extras group in `pyproject.toml`
- [x] Baseline report with per-class precision/recall/F1 +
      confusion matrices for val and test
- [x] v0.8.0 tag

### Per-frame label cleanup (shipped v0.9.0, did not lift baseline)
- [x] `training/youtube_corpus/relabel_per_frame.py` — zero-shot CLIP
      relabel over representative frames, with optional confidence
      gating via `--min-score` / `--min-margin`.
- [x] `--training-subdir` and `--artifact-subdir` overrides on
      `baseline_clip.py` for clean side-by-side variant training.
- [x] Honest hybrid evaluation (relabeled train, original val/test)
      to dodge the circular-eval trap.
- [x] Result: gated relabel moved val 0.216 to 0.257 (+19%) but test
      0.199 to 0.173 (-13%). Pure relabel hurt both. Zero-shot
      ViT-B/32 is too weak for this 9-way taxonomy; the experiment
      shipped because the harness is reusable for stronger backbones.
- [x] v0.9.0 tag

---

## Soon — weeks 2-4

### Stronger relabel signal (the v0.9.0 follow-up)
- [ ] Re-run `relabel_per_frame.py --model-id` against
      `google/siglip-large-patch16-384` or
      `laion/CLIP-ViT-H-14-laion2B-s32B-b79K` before abandoning the
      relabel approach. Same harness, different backbone.
- [ ] Active-learning bootstrap: stratified-diversity sample 50 frames
      per class, hand-label in the dashboard, retrain the baseline
      against the hand-labels as ground truth.
- [ ] Add `--calibration` flag to baseline trainer so the logits map
      to a confidence score the director loop can threshold.

### CroweLM shot-selector (the moat)
- [ ] `shot_selector(script_path, shoot_id)` — prompts the crowelm
      model with script sections + per-camera metadata (role, aspect,
      duration), returns an EDL (edit decision list).
- [ ] `render_edl(edl, output_path)` — ffmpeg concat + crossfade
      between selected angles per EDL entry.
- [ ] Training data collection: every time user overrides a shot-selector
      decision, save `{script_section, available_angles, chosen, reason}`
      tuple for future fine-tuning.

### Vertical auto-detection
- [ ] `smart_capture` snaps a still, classifies orientation via vision,
      picks 9:16 or 16:9 resolution before recording.

### Mike voice bridge
- [ ] New `studio_voice` tool call Mike as an oracle: "Mike, start wide
      and close cameras, route to toxicteetv." Mike parses, calls studio
      endpoints.

### Dashboard enhancements
- [ ] Chapter-marker keyboard shortcuts (1/2/3 during record)
- [ ] Keyboard shortcuts across app: `r` record, `s` stop, `z` zoom,
      `t` tenant switcher, `/` focus search
- [ ] Session history replay timeline with thumbnail previews
- [ ] Batch route to multiple tenants

### Tenant pipeline auto-advance
- [ ] After ingest, cron-trigger stages 02-03 on new manifests so the
      pipeline progresses overnight without manual intervention.

---

## Someday

### Hardware
- [ ] Custom iOS app that streams iPhone capture to the Mac over WiFi/
      USB so any number of iPhones can be active cameras (not limited
      to Continuity Camera's one-iPhone-at-a-time rule).
- [ ] Bluetooth foot-pedal integration for hands-free chapter marking.

### Cloud container fleet (the "central brain" architecture)
The vision: multiple iPhones at fixed angles feed a central Mac, while
cloud containers run the compute-heavy CroweLM models in parallel.

- [ ] `studio-cloud` Fly.io app: headless ffmpeg + CroweLM vision model,
      accepts shoot_id + clip uploads, returns scene analysis JSON
      (subject position, energy, orientation, aspect recommendation,
      platform-fit scores). Scales to N regions for multi-tenant use.
- [ ] Auth: short-lived JWTs signed by the Mac's control center so a
      hijacked cloud worker can't poison other tenants' shoots.
- [ ] Relay mode: cloud container also runs an NDI -> WebRTC bridge so
      remote iPhones on bad WiFi can route through the cloud instead of
      direct to the Mac. This is how on-location shoots work.
- [ ] Cost governor: cloud containers cost money; a per-shoot token
      budget prevents a runaway model from burning through credits.
- [ ] On-the-spot edit: cloud container receives the shoot bundle +
      script, runs CroweLM shot-selector, returns an EDL, central Mac
      renders using `render_edl`. This is the "edited by Crowe Logic
      in real time" feature from the original vision.

### Fine-tuned models
- [ ] `crowelm-studio` fine-tune: trained on every shoot's EDL overrides
      + script + final edit. Goal: 90%+ agreement with Michael's
      preferred shot choice after 50 shoots.
- [ ] `crowelm-vision-clip` fine-tune: multi-modal model that takes a
      10-second clip and returns {energy, subject_count, aspect_hint,
      usable_for: [platforms]}.

### Product
- [ ] Crowe Studio SaaS — tenant registry + CroweLM shot-selector as a
      subscription for other content creators. Single-seat $X/mo,
      agency tier $XXX/mo.
- [ ] Trademark filing once name is locked.
- [ ] Marketing landing page at studio.crowelogic.com.

### Integrations
- [ ] Ableton Link for synchronizing Talon Music Engine soundtracks
      with video edits.
- [ ] Direct OBS source plugin so Crowe Studio acts as a source in
      existing OBS rigs.
- [ ] Figma export of presentation stills for thumbnail design.
