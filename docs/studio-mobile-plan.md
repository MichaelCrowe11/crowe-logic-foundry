<!--
Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
Part of Crowe Studio | proprietary, private repository.
-->

# Crowe Studio | Mobile Companion Plan

Status: draft, 2026-04-22.
Owner: Michael Crowe / Crowe Logic, Inc.
Ships under: Crowe Studio v0.8.0 (first mobile milestone).

## 1. Goal

Turn a room full of iPhones and Android phones into a director-ed, AI-controlled film crew for Crowe Studio. The Mac stays the central brain. Each phone becomes a disciplined, network-attached camera that the CroweLM director can frame, focus, expose, and switch in real time.

## 2. Target platforms (decision baseline)

Defaults chosen pending override from Michael. Edit this block to change course.

```yaml
target_platforms:
  ios_minimum: "iOS 17"            # Continuity Camera era; matches Mac rig
  android_minimum: "Android 10"    # CameraX stable, ~95% device coverage
  distribution:
    ios: "app_store_public"        # paid or free, TBD at pricing decision
    android: "play_public"         # leverages existing Play posture
  priority: "parallel"             # one codebase serves both from day one
```

Rationale for each default:

- iOS 17 floor: matches the Mac-side Continuity Camera pipeline already in `tools/capture.py`. Phones older than this are rare in Michael's network and lack modern AVCaptureDevice zoom/focus APIs.
- Android 10 floor: CameraX APIs needed for programmatic focus + exposure stabilized at Android 10. Dropping below means per-vendor Camera2 quirks, which bleed dev time.
- Public distribution both: Crowe Logic AI already has a Play listing planned (per memory). Reusing that developer account avoids duplicate paperwork.
- Parallel priority: matches the recommended framework (Expo React Native with Vision Camera + react-native-webrtc), which produces both binaries from one source tree.

## 3. Framework decision

**Expo React Native** with `react-native-vision-camera` + `react-native-webrtc`.

Why not native-per-platform: Michael already has Expo tooling in place for Crowe Logic AI. The 10-15% camera-control ceiling gap vs native Swift/Kotlin is acceptable because the AI director runs on the Mac, not on the phone. The phone only needs to capture and obey.

Why not Flutter: no existing Flutter footprint in the Crowe portfolio; cost of adopting a new toolchain outweighs the ergonomic win.

Why not Kotlin Multiplatform: native UI + shared transport is architecturally cleanest but doubles the build surface; revisit for v2 if Vision Camera limits bite.

## 4. Architecture

```
  ┌──────────── Phones (N iPhones + M Androids) ────────────┐
  │  RN app: Vision Camera  →  WebRTC producer              │
  │          ▲                                              │
  │  control │ (data channel: focus point, zoom, exposure)  │
  └──────────┼──────────────────────────────────────────────┘
             │
             ▼
  ┌─────────────── Mac (central brain) ─────────────────────┐
  │  signaling   : FastAPI + aiortc  (port 8787)            │
  │  ingest      : per-phone track  →  ffmpeg record        │
  │  director    : CroweLM-Studio + Vision-Clip             │
  │                ├─ picks active angle                    │
  │                └─ emits camera commands back via DC     │
  │  renderer    : existing tools/edl_render.py             │
  │  routing     : existing tools/studio_route.py           │
  └──────────────────────────────────────────────────────────┘
```

Transport: WebRTC over LAN (ICE direct), fallback to Cloud TURN when phones are off-network. Only the Mac has the signaling key (`STUDIO_SIGNALING_TOKEN` in `~/.env.secrets`). Phones authenticate by presenting the token at connect time.

## 5. Phased build

### Phase 1 | Training pipeline first (no app yet)

Goal: have a usable CroweLM-Vision-Clip model trained on SW Mushrooms footage before the iPhone app reaches beta. This decouples model readiness from app readiness.

- [ ] `training/youtube_corpus/ingest.py` | yt-dlp wrapper that pulls the channel into `${TRAINING_CORPUS_DIR}/sw-mushrooms/videos` with archive tracking (no re-downloads).
- [ ] `training/youtube_corpus/frames.py` | ffmpeg frame sampling at 1 fps into `frames/<video_id>/`.
- [ ] `training/youtube_corpus/shots.py` | PySceneDetect run over each video, shot boundaries in `shots/<video_id>.json`.
- [ ] `training/youtube_corpus/label_shot_type.py` | first-pass labels with an off-the-shelf vision model (zero-shot CLIP or Qwen3-VL). Emits training JSONL.
- [ ] `training/shot_selector/train_vision_clip.py` | LoRA fine-tune on a rented A100 via HF Jobs or local M-series fallback.
- [ ] Manifest output: `training/youtube_corpus/sw-mushrooms/manifests/<run_id>.json` | reproducible record of what was trained and on what slice.

### Phase 2 | Mac signaling + ingest (still no app)

Goal: the Mac is ready to receive any WebRTC source, so phone devs can test against it without the app being finished.

- [ ] `tools/mobile_signaling.py` | FastAPI + aiortc. Endpoints: `POST /session`, `POST /offer/{camera_name}`, `POST /ice/{camera_name}`, `DELETE /session/{id}`. Auth: bearer `STUDIO_SIGNALING_TOKEN`.
- [ ] Camera registry extension: new `source_type: webrtc` in `config/studio_cameras.yaml`. Director treats these like any other source.
- [ ] Recorder: each incoming track goes to ffmpeg via a unix pipe so the existing capture + render pipeline keeps working unchanged.
- [ ] Smoke test: use a browser with getUserMedia to prove the Mac correctly records a remote track before writing any mobile code.

### Phase 3 | Mobile companion v1 (beta)

Goal: a single app that registers with the Mac, streams video, accepts camera commands. No UI polish, no Play listing yet.

- [ ] `mobile/` | Expo app scaffold in monorepo workspace.
- [ ] QR-pair flow: Mac dashboard shows QR with signaling URL + token; phone scans to join.
- [ ] Vision Camera capture at 1080p30 by default, 4k30 optional.
- [ ] WebRTC producer with data channel receiver: handles `{focus_point, zoom, exposure, torch, record_flag}`.
- [ ] Status overlay: tally light (record active), battery, thermal state.
- [ ] Internal test only. TestFlight + Play Internal Testing tracks.

### Phase 4 | Director integration + public release

- [ ] Real-time director loop: director polls each WebRTC track at 2 fps through Vision-Clip, emits camera commands + EDL entries.
- [ ] App store / Play polish: privacy policy URL, data safety form, screenshots, description.
- [ ] Trademark filed (blocked on name lock: "Crowe Studio" leading candidate).
- [ ] Public release behind paywall tier in Crowe Studio SaaS (see STUDIO_ROADMAP "Someday / Product").

## 6. Open decisions

Flagged for Michael. None block Phase 1.

1. **App name** | "Crowe Studio Camera" (descriptive) vs "Shotcall" (trademark candidate) vs standalone brand. Affects bundle id in `STUDIO_MOBILE_APP_BUNDLE_ID`.
2. **Paid vs free with subscription gate** | v1 mobile app could be free, with the *Mac* side being the paid product. Or the reverse.
3. **Cloud TURN provider** | Twilio, Cloudflare Calls, Metered.ca, or self-host Coturn on Fly.io. Matters only for off-network shoots.
4. **Android camera control depth** | Vision Camera exposes focus and exposure but zoom control via physical lens switching on multi-lens phones is platform-specific. Accept software zoom for v1, native lens switch for v2.
5. **Recording redundancy** | should each phone also record locally as a fallback against WiFi loss? Adds file-drop ingest complexity but saves a shoot. Default: yes, local redundant record.

## 7. Environment summary

Secrets (`~/.env.secrets`, chmod 600):
- `HF_TOKEN` | for HuggingFace model pulls and Jobs submissions.
- `STUDIO_SIGNALING_TOKEN` | bearer token phones present to the Mac.

Non-secret (`crowe-logic-foundry/.env`):
- `STUDIO_ROOT`, `CAPTURE_ROOT`, `TRAINING_CORPUS_DIR` | filesystem roots.
- `SW_MUSHROOMS_YT_CHANNEL_URL` | training corpus source.
- `STUDIO_SIGNALING_HOST`, `STUDIO_SIGNALING_PORT` | signaling server bind.
- `STUDIO_MOBILE_APP_BUNDLE_ID`, `STUDIO_MOBILE_APP_SCHEME` | reserved identifiers.

## 8. Next action

Kick off Phase 1.1: `training/youtube_corpus/ingest.py`. Verify with a dry-run listing of the channel before any download.
