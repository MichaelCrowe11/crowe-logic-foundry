"""
YouTube engagement tools: wraps the SWM Node.js triage system via subprocess.

The actual implementation lives in ~/Projects/southwest-mushrooms/agent/. These
wrappers expose it to the Foundry agent runtime so any brand-channel can be
triaged through the same agent (SWM today; ToxicTeeTv, Mick Raven, etc. tomorrow
once their Google OAuth is wired up).

Per-brand config is hardcoded for SWM right now. To support a second brand:
- Add a separate Keychain service name in youtube-auth.mjs (or parameterize).
- Per-brand voice profile + CTA bank are currently embedded in voice.py / draft
  files. Hoist into agents/<brand>-engagement.yaml as agent-level prompt.

Approval gate: post / spam / reject default to dry-run (apply=False). Caller
must explicitly pass apply=True to mutate the channel.
"""

import json
import os
import shlex
import subprocess
from pathlib import Path

SWM_AGENT_DIR = Path("/Users/crowelogic/Projects/southwest-mushrooms/agent")


def _run_node(script: str, *, env_extra: dict | None = None, timeout: int = 600) -> dict:
    """Run a Node script in the SWM agent dir and return parsed result.

    :param script: Script filename relative to SWM_AGENT_DIR.
    :param env_extra: Extra environment variables to pass.
    :param timeout: Seconds before subprocess is killed.
    :return: Dict with keys returncode, stdout, stderr.
    :rtype: dict
    """
    if not (SWM_AGENT_DIR / script).exists():
        return {"returncode": 1, "stdout": "", "stderr": f"missing script: {script}"}
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    try:
        cp = subprocess.run(
            ["node", script],
            cwd=str(SWM_AGENT_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {"returncode": cp.returncode, "stdout": cp.stdout, "stderr": cp.stderr}
    except subprocess.TimeoutExpired:
        return {"returncode": 124, "stdout": "", "stderr": f"timeout after {timeout}s"}


def youtube_pull_recent_comments(days: int = 30) -> str:
    """
    Pull recent top-level comments across the authenticated YouTube channel.
    Writes to agent/data/youtube-comments.json. Use this before triage.

    :param days: Number of days to look back. Default 30.
    :return: JSON summary with thread count and the data file path.
    :rtype: str
    """
    result = _run_node("pull-youtube-comments.mjs", env_extra={"DAYS": str(days)})
    if result["returncode"] != 0:
        return json.dumps({"error": result["stderr"][:500]})
    data_path = SWM_AGENT_DIR / "data" / "youtube-comments.json"
    if not data_path.exists():
        return json.dumps({"error": "pull succeeded but data file not found"})
    with open(data_path) as f:
        d = json.load(f)
    return json.dumps({
        "channel": d.get("channelTitle"),
        "channelId": d.get("channelId"),
        "threadCount": d.get("threadCount"),
        "sinceDays": d.get("sinceDays"),
        "dataPath": str(data_path),
    })


def youtube_pull_video_comments(video_id: str) -> str:
    """
    Pull all comments on a single video (use for Shorts or videos where
    channel-wide pull underreports).

    :param video_id: YouTube video id, e.g. "V_56UzLSZUY".
    :return: JSON summary with thread count and data file path.
    :rtype: str
    """
    result = _run_node(
        "pull-youtube-comments-by-video.mjs",
        env_extra={"VIDEO_ID": video_id},
    )
    if result["returncode"] != 0:
        return json.dumps({"error": result["stderr"][:500]})
    data_path = SWM_AGENT_DIR / "data" / f"youtube-comments.video-{video_id}.json"
    if not data_path.exists():
        return json.dumps({"error": "pull succeeded but data file not found"})
    with open(data_path) as f:
        d = json.load(f)
    return json.dumps({
        "videoId": video_id,
        "threadCount": d.get("threadCount"),
        "dataPath": str(data_path),
    })


def youtube_apply_triage(triage_file: str, apply: bool = False) -> str:
    """
    Apply a triage decision file (replies / spam / reject) via the existing
    apply-youtube-triage.mjs script. Default is dry-run; pass apply=True to
    actually post and mutate the channel.

    The triage_file is a path relative to agent/, e.g.
    "data/youtube-triage.priority-batch.approved.json".

    :param triage_file: Path to the triage JSON, relative to agent/.
    :param apply: If True, actually post. If False (default), dry-run.
    :return: JSON with the per-thread action results.
    :rtype: str
    """
    env = {"TRIAGE_FILE": triage_file}
    if apply:
        env["APPLY"] = "1"
    result = _run_node("apply-youtube-triage.mjs", env_extra=env, timeout=900)
    return json.dumps({
        "applied": apply,
        "returncode": result["returncode"],
        "stdout_tail": result["stdout"][-2000:],
        "stderr_tail": result["stderr"][-500:],
    })


def youtube_post_top_level_comment(video_id: str, text: str, apply: bool = False) -> str:
    """
    Post a NEW top-level comment on a video (e.g., a pinned-style comment with
    a link or FAQ). Pinning still requires manual action in YouTube Studio
    after posting. Default dry-run; pass apply=True to actually post.

    :param video_id: YouTube video id.
    :param text: Comment text. Plain text, no markdown.
    :param apply: If True, actually post.
    :return: JSON with status and the new comment id (if applied).
    :rtype: str
    """
    text_path = SWM_AGENT_DIR / "data" / f"_top_level_comment_{video_id}.txt"
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text(text.strip())
    env = {"VIDEO_ID": video_id, "TEXT_FILE": str(text_path)}
    if apply:
        env["APPLY"] = "1"
    result = _run_node("post-top-level-comment.mjs", env_extra=env, timeout=60)
    text_path.unlink(missing_ok=True)
    return json.dumps({
        "applied": apply,
        "videoId": video_id,
        "returncode": result["returncode"],
        "stdout": result["stdout"][:1500],
        "stderr": result["stderr"][:500],
    })
