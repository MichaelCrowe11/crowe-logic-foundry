import subprocess, sys, json, os


def _run(prompt, extra_args):
    proc = subprocess.run(
        [sys.executable, "-m", "cli.headless", "--model", "auto", *extra_args],
        input=json.dumps({"messages": [{"role": "user", "content": prompt}]}),
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc


def test_no_tools_flag_is_accepted():
    proc = _run("hi", ["--no-tools"])
    assert "unrecognized arguments" not in proc.stderr


def test_tools_flag_is_accepted():
    proc = _run("hi", ["--tools"])
    assert "unrecognized arguments" not in proc.stderr
