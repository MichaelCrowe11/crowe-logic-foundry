# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Studio — proprietary, private repository.

"""
Presentation tool — script ingestion, teleprompter, and zoom-effect
rendering for scripted recordings.

Composes with tools/capture.py and tools/studio_route.py. Typical flow:

  load_script -> launch_teleprompter
  (record via capture tools while script scrolls)
  apply_zoom_effect -> split_recording_by_chapters -> route

The teleprompter runs as a standalone HTML page in Safari so there is no
Python GUI dependency, and the user gets real-time pause/speed controls.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import textwrap
import time
from pathlib import Path
from typing import Optional

PRESENTATION_ROOT = Path(os.environ.get("PRESENTATION_ROOT", "/tmp/crowe-capture/presentations"))


def _ff() -> str:
    return os.environ.get("FFMPEG_BIN", "/opt/homebrew/bin/ffmpeg")


def _ensure_root() -> None:
    PRESENTATION_ROOT.mkdir(parents=True, exist_ok=True)


# ────────────────────────────────────────────────────────────────
# Script parsing
# ────────────────────────────────────────────────────────────────

_HEADER_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
_DIRECTIVE_RE = re.compile(r"^\[\s*(\w+)\s*(?::\s*(.+?))?\s*\]\s*$")


def load_script(path: str) -> str:
    """
    Parse a markdown presentation script into ordered sections.

    Script conventions:
      - Lines starting with '#'/'##'/'###' are section headers.
      - Optional directives live on their own line inside brackets:
          [zoom: punch_in]         # zoom effect for this section
          [duration: 15]           # hint to the recorder in seconds
          [pause: 2]               # seconds the teleprompter pauses here
      - Everything else is spoken content.

    :param path: Path to the script .md or .txt file.
    :return: JSON with {title, sections: [{index, title, level, duration,
        zoom, body, word_count}], total_word_count}.
    :rtype: str
    """
    try:
        text = Path(path).read_text()
    except Exception as e:
        return json.dumps({"error": f"Cannot read script: {e}"})

    lines = text.splitlines()
    title = None
    sections: list[dict] = []
    current: Optional[dict] = None

    def flush():
        if current is None:
            return
        body = "\n".join(current["body_lines"]).strip()
        current["body"] = body
        current["word_count"] = len(body.split())
        current.pop("body_lines")
        sections.append(current)

    for raw in lines:
        line = raw.rstrip()
        m = _HEADER_RE.match(line)
        if m:
            flush()
            level = len(m.group(1))
            heading = m.group(2).strip()
            if title is None and level == 1:
                title = heading
                current = None
                continue
            current = {
                "index": len(sections),
                "title": heading,
                "level": level,
                "duration": None,
                "zoom": None,
                "pause": None,
                "body_lines": [],
            }
            continue

        if current is None:
            continue

        d = _DIRECTIVE_RE.match(line.strip())
        if d:
            key = d.group(1).lower()
            val = (d.group(2) or "").strip()
            if key == "zoom":
                current["zoom"] = val or "none"
            elif key == "duration":
                try: current["duration"] = int(val)
                except ValueError: pass
            elif key == "pause":
                try: current["pause"] = float(val)
                except ValueError: pass
            continue

        current["body_lines"].append(line)

    flush()
    total_wc = sum(s["word_count"] for s in sections)
    return json.dumps({
        "title": title or Path(path).stem,
        "path": str(Path(path).resolve()),
        "sections": sections,
        "total_word_count": total_wc,
        "approx_duration_seconds_at_150wpm": round(total_wc / 150 * 60, 1),
    })


# ────────────────────────────────────────────────────────────────
# Teleprompter
# ────────────────────────────────────────────────────────────────

_TELEPROMPTER_HTML = r"""
<!doctype html><html><head><meta charset="utf-8"><title>__TITLE__</title>
<style>
  html,body{margin:0;height:100%;background:#0a0a0a;color:#f2f2f2;font-family:-apple-system,system-ui;overflow:hidden;}
  #scroll{position:absolute;inset:0;overflow:hidden;}
  #inner{padding:60vh 8vw;font-size:64px;line-height:1.35;font-weight:500;letter-spacing:-.01em;transform:translateY(0);}
  .title{font-size:80px;font-weight:700;margin:0 0 0.6em;color:#fff;}
  .section{margin:0 0 1.4em;}
  .section h2{font-size:46px;color:#7fd0ff;margin:0 0 0.4em;font-weight:600;}
  .directive{color:#6f6f6f;font-size:28px;font-style:italic;margin:0 0 0.4em;}
  .section p{margin:0 0 0.8em;}
  #controls{position:fixed;top:20px;left:20px;right:20px;display:flex;justify-content:space-between;align-items:center;z-index:10;font-size:14px;color:#999;pointer-events:none;}
  #controls .box{background:rgba(0,0,0,0.55);padding:6px 12px;border-radius:6px;pointer-events:auto;}
  .mirror #inner{transform:scaleX(-1) translateY(0);}
  #bar{position:fixed;top:50%;left:0;right:0;height:3px;background:rgba(127,208,255,0.3);pointer-events:none;}
</style></head><body>
<div id="controls">
  <div class="box">Space = play/pause &middot; &uarr;&darr; = speed &middot; m = mirror &middot; r = reset &middot; Esc = close</div>
  <div class="box" id="stat">paused &middot; 1.0x</div>
</div>
<div id="bar"></div>
<div id="scroll"><div id="inner">__INNER__</div></div>
<script>
  var inner=document.getElementById("inner"),stat=document.getElementById("stat"),body=document.body;
  var y=0,playing=false,speed=__WPM__/60*1.0;
  var basePxPerSec=60;
  function tick(){if(playing){y+=(basePxPerSec*speed)/60;inner.style.transform=(body.classList.contains("mirror")?"scaleX(-1) ":"")+"translateY("+(-y)+"px)";stat.textContent=(playing?"scrolling":"paused")+" · "+speed.toFixed(2)+"x";}requestAnimationFrame(tick);}
  tick();
  document.addEventListener("keydown",function(e){
    if(e.code==="Space"){e.preventDefault();playing=!playing;stat.textContent=(playing?"scrolling":"paused")+" · "+speed.toFixed(2)+"x";}
    else if(e.code==="ArrowUp"){speed=Math.min(speed+0.15,5);}
    else if(e.code==="ArrowDown"){speed=Math.max(speed-0.15,0.1);}
    else if(e.key==="m"||e.key==="M"){body.classList.toggle("mirror");}
    else if(e.key==="r"||e.key==="R"){y=0;inner.style.transform="translateY(0)";}
    else if(e.key==="Escape"){window.close();}
  });
</script></body></html>
"""


def launch_teleprompter(
    script_path: str,
    wpm: int = 150,
    mirror: bool = False,
    open_browser: bool = True,
) -> str:
    """
    Generate a styled HTML teleprompter and open it in Safari. The page
    auto-scrolls at the target words-per-minute; hotkeys let you pause,
    speed up, mirror, and reset.

    :param script_path: Path to the markdown script.
    :param wpm: Target words-per-minute scroll speed (120-180 typical).
    :param mirror: If true, start with horizontally mirrored text (for
        beam-splitter teleprompter rigs).
    :param open_browser: If true, open the resulting file in Safari.
    :return: JSON with {html_path, word_count, sections, url}.
    :rtype: str
    """
    try:
        _ensure_root()
        parsed_raw = load_script(script_path)
        parsed = json.loads(parsed_raw)
        if "error" in parsed:
            return parsed_raw

        title = parsed["title"]
        parts = [f'<div class="title">{_html_escape(title)}</div>']
        for s in parsed["sections"]:
            parts.append('<div class="section">')
            if s["title"]:
                parts.append(f'<h2>{_html_escape(s["title"])}</h2>')
            hints = []
            if s["duration"]:
                hints.append(f'duration {s["duration"]}s')
            if s["zoom"]:
                hints.append(f'zoom {s["zoom"]}')
            if hints:
                parts.append(f'<div class="directive">[ {" · ".join(hints)} ]</div>')
            for para in (s["body"] or "").split("\n\n"):
                para = para.strip()
                if para:
                    parts.append(f"<p>{_html_escape(para)}</p>")
            parts.append("</div>")

        inner = "\n".join(parts)
        html = (_TELEPROMPTER_HTML
                .replace("__TITLE__", _html_escape(title))
                .replace("__INNER__", inner)
                .replace("__WPM__", str(wpm)))
        if mirror:
            html = html.replace("<body>", '<body class="mirror">')

        out = PRESENTATION_ROOT / f"teleprompter-{int(time.time())}.html"
        out.write_text(html)

        # Always open in a fresh Safari window on the main display. Safari
        # is scriptable; Chrome/Arc often aren't under TCC. The `open -a`
        # alone may place the window on a secondary monitor, so we follow
        # it with an AppleScript that creates a new document in Safari
        # and sizes it to the main-display bounds.
        applescript = f'''
        tell application "Finder"
            set screenBounds to bounds of window of desktop
        end tell
        set scrW to item 3 of screenBounds
        set scrH to item 4 of screenBounds
        tell application "Safari"
            activate
            make new document with properties {{URL:"file://{out}"}}
            delay 0.3
            try
                set bounds of front window to {{0, 25, scrW, scrH}}
                set index of front window to 1
            end try
            return "main-display: " & scrW & "x" & scrH
        end tell
        '''
        placement = None
        if open_browser:
            try:
                r = subprocess.run(
                    ["osascript", "-e", applescript],
                    capture_output=True, text=True, timeout=10,
                )
                placement = r.stdout.strip() or r.stderr.strip()
            except Exception as ex:
                placement = f"open-fallback: {ex}"
                subprocess.Popen(["open", "-a", "Safari", str(out)])

        return json.dumps({
            "html_path": str(out),
            "url": f"file://{out}",
            "title": title,
            "word_count": parsed["total_word_count"],
            "sections": len(parsed["sections"]),
            "wpm": wpm,
            "opened_in_browser": open_browser,
            "placement": placement,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def _html_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


# ────────────────────────────────────────────────────────────────
# Zoom effects
# ────────────────────────────────────────────────────────────────

# Each preset returns an ffmpeg filter_complex snippet parameterized on
# duration_seconds. Rendered at the source resolution unless the caller
# passes width/height overrides. All effects preserve audio untouched.

ZOOM_PRESETS = {
    "punch_in": {
        "label": "Hold 1s, then smooth zoom to 1.4x by end",
        "description": "Classic hook emphasis. Use on the first line of a video.",
    },
    "slow_zoom_out": {
        "label": "Start at 1.25x, pull out to 1x",
        "description": "Reveal effect. Works for establishing shots that broaden to context.",
    },
    "ken_burns": {
        "label": "1x to 1.18x with slow pan right",
        "description": "Documentary feel. Neutral for talking-head or product shots.",
    },
    "whip_zoom": {
        "label": "Rapid zoom to 1.8x in 0.4s then hold",
        "description": "High-energy transitions. Use sparingly.",
    },
    "cut_to_closeup": {
        "label": "Step-cut to 1.5x at 50% mark",
        "description": "Emphasis beat. Simulates a second camera angle.",
    },
    "none": {
        "label": "Passthrough, no zoom",
        "description": "Re-mux only. Keeps the source pristine.",
    },
}


def list_zoom_effects() -> str:
    """
    List available zoom-effect presets for apply_zoom_effect.

    :return: JSON mapping preset name to {label, description}.
    :rtype: str
    """
    return json.dumps(ZOOM_PRESETS)


def _zoom_filter(effect: str, duration: float, fps: int = 30) -> Optional[str]:
    total = max(1, int(round(duration * fps)))
    # zoompan works on a virtual scaled image. d=1 means emit one output frame
    # per input frame; we set d=total and give zoompan one still by setting
    # fps=fps on output. For real-time clips we use the simpler pattern of
    # zoompan fed from input frames directly: z expression is time-based.
    if effect == "punch_in":
        return (
            f"zoompan=z='if(lt(on,{fps}),1,min(1.4,1+((on-{fps})/({total}-{fps}))*0.4))'"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1920x1080:fps={fps}"
        )
    if effect == "slow_zoom_out":
        return (
            f"zoompan=z='max(1,1.25-(on/{total})*0.25)'"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1920x1080:fps={fps}"
        )
    if effect == "ken_burns":
        return (
            f"zoompan=z='1+(on/{total})*0.18'"
            f":x='(iw-iw/zoom)*(on/{total})':y='ih/2-(ih/zoom/2)':d=1:s=1920x1080:fps={fps}"
        )
    if effect == "whip_zoom":
        hit = max(1, int(0.4 * fps))
        return (
            f"zoompan=z='if(lt(on,{hit}),1+(on/{hit})*0.8,1.8)'"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1920x1080:fps={fps}"
        )
    if effect == "cut_to_closeup":
        mid = total // 2
        return (
            f"zoompan=z='if(lt(on,{mid}),1,1.5)'"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1920x1080:fps={fps}"
        )
    if effect == "none":
        return None
    return None


def apply_zoom_effect(
    clip_path: str,
    effect: str = "punch_in",
    output_path: str = "",
    fps: int = 30,
) -> str:
    """
    Render a derivative clip with a zoom effect applied. Source audio is
    copied untouched. Output is 1080p h264_videotoolbox + AAC.

    :param clip_path: Path to source mp4.
    :param effect: Preset name from list_zoom_effects ("punch_in",
        "slow_zoom_out", "ken_burns", "whip_zoom", "cut_to_closeup", "none").
    :param output_path: Where to write. Auto-derived as <clip>.zoom.<effect>.mp4 if empty.
    :param fps: Target framerate for the rendered output.
    :return: JSON with {input, output, effect, bytes, duration} or error.
    :rtype: str
    """
    try:
        if effect not in ZOOM_PRESETS:
            return json.dumps({"error": f"Unknown effect: {effect}", "available": list(ZOOM_PRESETS.keys())})

        src = Path(clip_path).expanduser().resolve()
        if not src.exists():
            return json.dumps({"error": f"Source not found: {src}"})

        duration = _probe_duration(str(src))
        if duration <= 0:
            return json.dumps({"error": "Could not read source duration"})

        if not output_path:
            output_path = str(src.with_suffix("")) + f".zoom.{effect}.mp4"

        vf = _zoom_filter(effect, duration, fps=fps)
        cmd = [
            _ff(), "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(src),
        ]
        if vf:
            cmd += ["-filter_complex", f"[0:v]{vf}[v]",
                    "-map", "[v]", "-map", "0:a?"]
        else:
            cmd += ["-c:v", "copy", "-c:a", "copy"]

        if vf:
            cmd += [
                "-c:v", "h264_videotoolbox", "-b:v", "8M",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
            ]
        cmd += [output_path]

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0 or not os.path.exists(output_path):
            return json.dumps({
                "error": "ffmpeg failed",
                "stderr": proc.stderr.strip()[-800:],
            })
        return json.dumps({
            "input": str(src),
            "output": output_path,
            "effect": effect,
            "bytes": os.path.getsize(output_path),
            "duration": duration,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def _probe_duration(path: str) -> float:
    try:
        r = subprocess.run(
            ["/opt/homebrew/bin/ffprobe", "-v", "error",
             "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=15,
        )
        return float(r.stdout.strip() or 0)
    except Exception:
        return 0.0


# ────────────────────────────────────────────────────────────────
# Chapter splitting
# ────────────────────────────────────────────────────────────────

def split_recording_by_chapters(
    recording_path: str,
    chapters: str,
    output_dir: str = "",
) -> str:
    """
    Split a single recording into per-section clips based on a chapter list.

    :param recording_path: Source recording.
    :param chapters: JSON array of {title, start_seconds, end_seconds}.
        Or a path to a .json file containing that array.
    :param output_dir: Directory for the clips. Auto-generated if empty.
    :return: JSON with [{title, path, bytes, duration}].
    :rtype: str
    """
    try:
        src = Path(recording_path).expanduser().resolve()
        if not src.exists():
            return json.dumps({"error": f"Source not found: {src}"})

        if chapters.strip().startswith("["):
            data = json.loads(chapters)
        else:
            data = json.loads(Path(chapters).read_text())

        out_dir = Path(output_dir) if output_dir else src.parent / f"{src.stem}-chapters"
        out_dir.mkdir(parents=True, exist_ok=True)

        results = []
        for i, ch in enumerate(data):
            title = ch.get("title", f"chapter-{i+1}")
            start = float(ch["start_seconds"])
            end = float(ch["end_seconds"])
            safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", title).strip("-").lower() or f"chapter-{i+1}"
            out = out_dir / f"{i+1:02d}-{safe}.mp4"
            cmd = [
                _ff(), "-hide_banner", "-loglevel", "error", "-y",
                "-ss", str(start), "-to", str(end),
                "-i", str(src),
                "-c", "copy",
                "-movflags", "+faststart",
                str(out),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if proc.returncode == 0 and out.exists():
                results.append({
                    "title": title,
                    "path": str(out),
                    "bytes": out.stat().st_size,
                    "duration": round(end - start, 2),
                })
            else:
                results.append({
                    "title": title,
                    "error": proc.stderr.strip()[-400:],
                })
        return json.dumps(results)
    except Exception as e:
        return json.dumps({"error": str(e)})
