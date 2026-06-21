from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class VerifyResult:
    passed: bool
    exit_code: int | None
    timed_out: bool
    output: str


def _verify_env() -> dict:
    """Expose the harness interpreter (the one with pytest installed) as the
    `python`/`python3` resolved by verify.sh, by prepending its bin/ to PATH.
    The bare system python3 on macOS has no pytest, so verify.sh would error."""
    env = dict(os.environ)
    bindir = str(Path(sys.executable).parent)
    env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
    return env


def run_verify(verify_sh: Path, workdir: Path, timeout_s: int) -> VerifyResult:
    dest = Path(workdir) / "_verify.sh"
    shutil.copyfile(verify_sh, dest)
    dest.chmod(0o755)
    try:
        proc = subprocess.run(
            ["/bin/sh", str(dest)],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=_verify_env(),
        )
    except subprocess.TimeoutExpired as e:
        out = e.output if isinstance(e.output, str) else ""
        return VerifyResult(False, None, True, out or "")
    finally:
        dest.unlink(missing_ok=True)
    return VerifyResult(
        passed=proc.returncode == 0,
        exit_code=proc.returncode,
        timed_out=False,
        output=(proc.stdout + proc.stderr)[-4000:],
    )
