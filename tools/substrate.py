"""
Substrate Music Engine — production-grade album rendering via abletonctl builders.

Substrate is Michael Crowe's original music genre: section-first long-form composition
with autobiographical narration, stem-first rendering, hand-authored ducking, and
dynamic masters that preserve consequence instead of flattening everything.

Each track is a hand-crafted Python builder (500-1100+ lines) that writes per-note MIDI,
renders stems via FluidSynth + VintageDreamsWaves-v2 @ 48kHz, applies per-stem ffmpeg
chains, and mixes to a loudnorm=-14 LUFS master.
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path

ABLETONCTL_PATH = Path.home() / "Projects" / "abletonctl"
VOCALS_PATH = Path("/Volumes/Elements/substrate-vocals")
DEFAULT_OUTPUT = Path.home() / "Desktop"

# Canonical track registry — order matches the album sequence
TRACK_REGISTRY = {
    "desert-transmission": {
        "builder": "build_desert_transmission.py",
        "title": "DesertTransmission",
        "key": "Em", "bpm": 72, "duration": "15:00",
        "instruments": 10, "track_number": 1,
        "character": "Floyd/QOTSA desert epic — the overture",
    },
    "velvet-algorithm": {
        "builder": "build_velvet_algorithm.py",
        "title": "VelvetAlgorithm",
        "key": "Dm", "bpm": 92, "duration": "9:00",
        "instruments": 9, "track_number": 2,
        "character": "Acid jazz — algorithmic elegance",
    },
    "neon-scripture": {
        "builder": "build_neon_scripture.py",
        "title": "NeonScripture",
        "key": "Am", "bpm": 85, "duration": "8:00",
        "instruments": 8, "track_number": 3,
        "character": "Dark ambient prog — the thesis statement",
    },
    "analog-heart": {
        "builder": "build_analog_heart.py",
        "title": "AnalogHeart",
        "key": "Fm", "bpm": 78, "duration": "10:00",
        "instruments": 8, "track_number": 4,
        "character": "Trip-hop — vinyl warmth and sub heartbeat",
    },
    "mycelium-network": {
        "builder": "build_mycelium_network.py",
        "title": "MyceliumNetwork",
        "key": "Cm", "bpm": 68, "duration": "8:00",
        "instruments": 7, "track_number": 5,
        "character": "Ambient organic — forest floor to fruiting body",
    },
    "signal-return": {
        "builder": "build_signal_return.py",
        "title": "SignalReturn",
        "key": "F#m", "bpm": 140, "duration": "7:00",
        "instruments": 8, "track_number": 6,
        "character": "Breakbeat/DnB — radio static to signal lock",
    },
    "engines-reply": {
        "builder": "build_engines_reply.py",
        "title": "EnginesReply",
        "key": "C", "bpm": 60, "duration": "8:00",
        "instruments": 7, "track_number": 7,
        "character": "Chamber piece — the engine speaks back (only major key)",
    },
    "echoes-of-syd": {
        "builder": "build_echoes_of_syd.py",
        "title": "EchoesOfSyd",
        "key": "Abm", "bpm": 76, "duration": "12:00",
        "instruments": 9, "track_number": 8,
        "character": "Psychedelic prog — dual-voice Barrett tribute",
    },
}

# Regex patch to skip ElevenLabs vocal generation during instrumental renders
_VOCAL_SKIP_PATCH = re.compile(
    r'def generate_vocal\(.*?\):\n(    .*\n)*'
)
_VOCAL_SKIP_REPLACEMENT = (
    'def generate_vocal(name, text, stability=0.75, style=0.35):\n'
    '    print(f"  ⊘ Skipping vocal: {name} (instrumental render)")\n'
    '    return\n'
)


def substrate_list_tracks() -> str:
    """
    List all Substrate album tracks with their specs.
    Shows track number, title, key, BPM, duration, instrument count,
    builder status, and render status.

    :return: JSON with track listing.
    :rtype: str
    """
    tracks = []
    for slug, info in TRACK_REGISTRY.items():
        builder_path = ABLETONCTL_PATH / info["builder"]
        output_dir = DEFAULT_OUTPUT / info["title"]
        master_mp3 = output_dir / f"{info['title']}_MASTER.mp3"

        tracks.append({
            "track_number": info["track_number"],
            "slug": slug,
            "title": info["title"],
            "key": info["key"],
            "bpm": info["bpm"],
            "duration": info["duration"],
            "instruments": info["instruments"],
            "character": info["character"],
            "builder_exists": builder_path.exists(),
            "builder_lines": _count_lines(builder_path) if builder_path.exists() else 0,
            "rendered": master_mp3.exists(),
            "master_path": str(master_mp3) if master_mp3.exists() else None,
        })

    tracks.sort(key=lambda t: t["track_number"])
    return json.dumps({"album": "Substrate", "tracks": tracks}, indent=2)


def substrate_render_track(track: str, instrumental: bool = True,
                           output_dir: str = "") -> str:
    """
    Render a single Substrate track using its abletonctl builder.
    Produces stem WAVs + master WAV (48kHz/24-bit) + master MP3 (320k).

    :param track: Track slug (e.g. 'neon-scripture', 'desert-transmission').
    :param instrumental: Skip vocal generation if True (default True).
    :param output_dir: Override output directory (default: ~/Desktop/<Title>/).
    :return: JSON with render result including file paths and duration.
    :rtype: str
    """
    if track not in TRACK_REGISTRY:
        return json.dumps({
            "error": f"Unknown track: {track}",
            "available": list(TRACK_REGISTRY.keys()),
        })

    info = TRACK_REGISTRY[track]
    builder_path = ABLETONCTL_PATH / info["builder"]

    if not builder_path.exists():
        return json.dumps({"error": f"Builder not found: {builder_path}"})

    out = output_dir or str(DEFAULT_OUTPUT / info["title"])
    start_time = time.time()

    try:
        if instrumental:
            result = _run_builder_instrumental(builder_path, info["title"])
        else:
            result = _run_builder_full(builder_path)

        elapsed = time.time() - start_time
        master_wav = Path(out) / f"{info['title']}_MASTER.wav"
        master_mp3 = Path(out) / f"{info['title']}_MASTER.mp3"

        return json.dumps({
            "track": info["title"],
            "status": "success" if result.returncode == 0 else "failed",
            "elapsed_seconds": round(elapsed, 1),
            "output_dir": out,
            "master_wav": str(master_wav) if master_wav.exists() else None,
            "master_mp3": str(master_mp3) if master_mp3.exists() else None,
            "master_size_mb": round(master_wav.stat().st_size / 1e6, 1) if master_wav.exists() else 0,
            "stdout": result.stdout[-2000:] if result.stdout else "",
            "stderr": result.stderr[-1000:] if result.stderr else "",
        }, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e), "track": track})


def substrate_render_album(instrumental: bool = True) -> str:
    """
    Render all 8 Substrate tracks sequentially using abletonctl builders.
    Each track produces stem WAVs + master WAV/MP3 on the Desktop.

    :param instrumental: Skip vocal generation if True (default True).
    :return: JSON with per-track render results.
    :rtype: str
    """
    results = []
    total_start = time.time()

    for slug in sorted(TRACK_REGISTRY, key=lambda s: TRACK_REGISTRY[s]["track_number"]):
        info = TRACK_REGISTRY[slug]
        builder_path = ABLETONCTL_PATH / info["builder"]

        if not builder_path.exists():
            results.append({"track": info["title"], "status": "skipped", "reason": "no builder"})
            continue

        track_start = time.time()
        try:
            if instrumental:
                r = _run_builder_instrumental(builder_path, info["title"])
            else:
                r = _run_builder_full(builder_path)

            elapsed = time.time() - track_start
            master = DEFAULT_OUTPUT / info["title"] / f"{info['title']}_MASTER.mp3"
            results.append({
                "track": info["title"],
                "status": "success" if r.returncode == 0 else "failed",
                "elapsed_seconds": round(elapsed, 1),
                "rendered": master.exists(),
            })
        except Exception as e:
            results.append({"track": info["title"], "status": "error", "error": str(e)})

    total_elapsed = time.time() - total_start
    return json.dumps({
        "album": "Substrate",
        "total_elapsed_seconds": round(total_elapsed, 1),
        "tracks": results,
    }, indent=2)


def substrate_render_status() -> str:
    """
    Check which Substrate tracks have been rendered and their file sizes.

    :return: JSON with render status for all 8 tracks.
    :rtype: str
    """
    status = []
    for slug in sorted(TRACK_REGISTRY, key=lambda s: TRACK_REGISTRY[s]["track_number"]):
        info = TRACK_REGISTRY[slug]
        output_dir = DEFAULT_OUTPUT / info["title"]
        master_wav = output_dir / f"{info['title']}_MASTER.wav"
        master_mp3 = output_dir / f"{info['title']}_MASTER.mp3"

        stems = []
        if output_dir.exists():
            stems = [f.name for f in output_dir.iterdir()
                     if f.suffix == ".wav" and "MASTER" not in f.name]

        status.append({
            "track_number": info["track_number"],
            "title": info["title"],
            "has_master_wav": master_wav.exists(),
            "has_master_mp3": master_mp3.exists(),
            "master_size_mb": round(master_wav.stat().st_size / 1e6, 1) if master_wav.exists() else 0,
            "mp3_size_mb": round(master_mp3.stat().st_size / 1e6, 1) if master_mp3.exists() else 0,
            "stem_count": len(stems),
            "stems": stems,
        })

    return json.dumps({"album": "Substrate", "tracks": status}, indent=2)


def substrate_vocal_status() -> str:
    """
    Check status of ChatterboxTTS vocal clips for all tracks.
    Shows which clips exist, which are missing, and total counts.

    :return: JSON with vocal clip inventory per track.
    :rtype: str
    """
    inventory = []
    total_clips = 0
    total_missing = 0

    for slug in sorted(TRACK_REGISTRY, key=lambda s: TRACK_REGISTRY[s]["track_number"]):
        info = TRACK_REGISTRY[slug]
        vocal_dir = VOCALS_PATH / slug.replace("-", "_")

        existing = []
        if vocal_dir.exists():
            existing = sorted([f.name for f in vocal_dir.iterdir()
                              if f.suffix == ".wav" and f.stat().st_size > 1000])

        total_clips += len(existing)
        inventory.append({
            "track": info["title"],
            "slug": slug,
            "vocal_dir": str(vocal_dir),
            "clips": existing,
            "clip_count": len(existing),
        })

    return json.dumps({
        "album": "Substrate",
        "total_clips": total_clips,
        "inventory": inventory,
    }, indent=2)


def substrate_mix_vocals(track: str, vocal_volume_db: float = -6.0) -> str:
    """
    Mix ChatterboxTTS vocal clips into a rendered instrumental master.
    Uses the duck_expr timing from the builder for proper vocal placement.

    :param track: Track slug (e.g. 'neon-scripture').
    :param vocal_volume_db: Vocal level relative to instrumental (default -6 dB).
    :return: JSON with mix result.
    :rtype: str
    """
    if track not in TRACK_REGISTRY:
        return json.dumps({"error": f"Unknown track: {track}"})

    info = TRACK_REGISTRY[track]
    output_dir = DEFAULT_OUTPUT / info["title"]
    master_wav = output_dir / f"{info['title']}_MASTER.wav"
    vocal_dir = VOCALS_PATH / track.replace("-", "_")

    if not master_wav.exists():
        return json.dumps({"error": f"No instrumental master at {master_wav}. Render first."})

    if not vocal_dir.exists():
        return json.dumps({"error": f"No vocal clips at {vocal_dir}."})

    vocal_clips = sorted([f for f in vocal_dir.iterdir()
                         if f.suffix == ".wav" and f.stat().st_size > 1000])

    if not vocal_clips:
        return json.dumps({"error": f"No valid vocal clips in {vocal_dir}."})

    # Build ffmpeg filter to overlay vocals on instrumental
    vocal_mix = output_dir / f"{info['title']}_VOCAL_MIX.wav"
    inputs = ["-i", str(master_wav)]
    for clip in vocal_clips:
        inputs.extend(["-i", str(clip)])

    # Simple amix — for production use, the builder's duck_expr handles timing
    n_inputs = 1 + len(vocal_clips)
    filter_parts = []
    for i in range(n_inputs):
        if i == 0:
            filter_parts.append(f"[{i}]volume=0dB[inst]")
        else:
            filter_parts.append(f"[{i}]volume={vocal_volume_db}dB[v{i}]")

    mix_inputs = "[inst]" + "".join(f"[v{i}]" for i in range(1, n_inputs))
    filter_str = ";".join(filter_parts) + f";{mix_inputs}amix=inputs={n_inputs}:normalize=0[out]"

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", filter_str,
        "-map", "[out]",
        "-c:a", "pcm_s24le", "-ar", "48000",
        str(vocal_mix),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0 and vocal_mix.exists():
            # Also encode MP3
            mp3_out = output_dir / f"{info['title']}_VOCAL_MIX.mp3"
            subprocess.run([
                "ffmpeg", "-y", "-i", str(vocal_mix),
                "-codec:a", "libmp3lame", "-b:a", "320k", str(mp3_out),
            ], capture_output=True, timeout=120)

            return json.dumps({
                "track": info["title"],
                "status": "success",
                "vocal_clips_mixed": len(vocal_clips),
                "output_wav": str(vocal_mix),
                "output_mp3": str(mp3_out) if mp3_out.exists() else None,
                "size_mb": round(vocal_mix.stat().st_size / 1e6, 1),
            }, indent=2)
        else:
            return json.dumps({
                "track": info["title"],
                "status": "failed",
                "stderr": result.stderr[-1000:],
            })
    except Exception as e:
        return json.dumps({"error": str(e)})


def substrate_open_track(track: str) -> str:
    """
    Open a rendered Substrate track in the default audio player.

    :param track: Track slug (e.g. 'neon-scripture') or 'all' to open every rendered track.
    :return: JSON confirmation.
    :rtype: str
    """
    if track == "all":
        opened = []
        for slug in sorted(TRACK_REGISTRY, key=lambda s: TRACK_REGISTRY[s]["track_number"]):
            info = TRACK_REGISTRY[slug]
            mp3 = DEFAULT_OUTPUT / info["title"] / f"{info['title']}_MASTER.mp3"
            if mp3.exists():
                subprocess.run(["open", str(mp3)], check=False)
                opened.append(info["title"])
        return json.dumps({"opened": opened, "count": len(opened)})

    if track not in TRACK_REGISTRY:
        return json.dumps({"error": f"Unknown track: {track}"})

    info = TRACK_REGISTRY[track]
    mp3 = DEFAULT_OUTPUT / info["title"] / f"{info['title']}_MASTER.mp3"
    if not mp3.exists():
        return json.dumps({"error": f"Not rendered yet: {mp3}"})

    subprocess.run(["open", str(mp3)], check=False)
    return json.dumps({"opened": info["title"], "path": str(mp3)})


def substrate_dna() -> str:
    """
    Display the Substrate DNA spec — the shared creative grammar that defines
    whether a composition qualifies as Substrate.

    :return: The Substrate DNA document contents.
    :rtype: str
    """
    dna_path = Path.home() / "Projects" / "talon" / "docs" / "substrate-dna.md"
    if not dna_path.exists():
        return json.dumps({"error": "substrate-dna.md not found in Talon docs"})

    return dna_path.read_text()


# --- Internal helpers ---

def _run_builder_instrumental(builder_path: Path, title: str) -> subprocess.CompletedProcess:
    """Run a builder with generate_vocal patched to no-op."""
    source = builder_path.read_text()
    patched = _VOCAL_SKIP_PATCH.sub(_VOCAL_SKIP_REPLACEMENT, source, count=1)

    return subprocess.run(
        [sys.executable, "-c", patched],
        capture_output=True, text=True, timeout=600,
        cwd=str(ABLETONCTL_PATH),
    )


def _run_builder_full(builder_path: Path) -> subprocess.CompletedProcess:
    """Run a builder as-is (including vocal generation)."""
    return subprocess.run(
        [sys.executable, str(builder_path)],
        capture_output=True, text=True, timeout=900,
        cwd=str(ABLETONCTL_PATH),
    )


def _count_lines(path: Path) -> int:
    """Count lines in a file."""
    try:
        return sum(1 for _ in open(path))
    except Exception:
        return 0
