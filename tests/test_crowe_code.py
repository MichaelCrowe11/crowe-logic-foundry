"""Tests for tools/crowe_code.py — Crowe Code editor-block bridge.

These never touch a real `wsh` binary or the real home dir: the wsh seam
(`_run_wsh`), the runtime gate (`in_crowe_terminal`), the shell executor
(`execute_shell`), and the scratch-buffer dir (`BUFFER_DIR`) are all
monkeypatched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.crowe_code as cc


# ---------------------------------------------------------------------------
# Helpers — build a fake wsh dispatcher keyed on the subcommand
# ---------------------------------------------------------------------------


def make_wsh(handlers):
    """Return a fake _run_wsh(args, timeout=...) -> (rc, stdout, stderr).

    `handlers` maps the wsh subcommand (args[0]) to either a (rc, out, err)
    tuple or a callable(args) -> (rc, out, err).
    """

    def _fake(args, timeout=10):
        sub = args[0] if args else ""
        h = handlers.get(sub)
        if h is None:
            return (1, "", f"no handler for {sub}")
        return h(args) if callable(h) else h

    return _fake


@pytest.fixture()
def in_terminal(monkeypatch):
    """Force the runtime gate on so tool bodies run."""
    monkeypatch.setattr(cc, "in_crowe_terminal", lambda: True)
    monkeypatch.setattr(cc, "_crowe_code_capable", lambda: True)


UUID = "d03e0bea-a89a-4b3b-94b0-1b6680664b2c"


# ---------------------------------------------------------------------------
# Runtime gate
# ---------------------------------------------------------------------------


class TestRuntimeGate:
    def test_false_without_jwt(self, monkeypatch):
        monkeypatch.delenv("WAVETERM_JWT", raising=False)
        monkeypatch.setattr(cc, "_wsh_path", lambda: "/usr/bin/wsh")
        assert cc.in_crowe_terminal() is False

    def test_false_without_wsh(self, monkeypatch):
        monkeypatch.setenv("WAVETERM_JWT", "tok")
        monkeypatch.setattr(cc, "_wsh_path", lambda: None)
        assert cc.in_crowe_terminal() is False

    def test_true_with_jwt_and_wsh(self, monkeypatch):
        monkeypatch.setenv("WAVETERM_JWT", "tok")
        monkeypatch.setattr(cc, "_wsh_path", lambda: "/usr/bin/wsh")
        assert cc.in_crowe_terminal() is True


# ---------------------------------------------------------------------------
# System-prompt addendum
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    def test_empty_when_not_in_terminal(self, monkeypatch):
        monkeypatch.setattr(cc, "in_crowe_terminal", lambda: False)
        assert cc.system_prompt() == ""

    def test_present_in_terminal(self, in_terminal):
        text = cc.system_prompt()
        assert "Crowe Code" in text
        assert "crowecode:file" in text
        assert "crowe_code_read_block" in text
        assert "UUID" in text


# ---------------------------------------------------------------------------
# read_block
# ---------------------------------------------------------------------------


class TestReadBlock:
    def test_returns_file_contents(self, monkeypatch, in_terminal, tmp_path):
        f = tmp_path / "workflow.py"
        f.write_text("print('hello from block')\n")
        monkeypatch.setattr(
            cc,
            "_run_wsh",
            make_wsh({"getmeta": (0, str(f) + "\n", "")}),
        )
        result = json.loads(cc.crowe_code_read_block(UUID))
        assert result["backed_by_file"] is True
        assert result["file"] == str(f)
        assert "hello from block" in result["content"]

    def test_scratch_buffer_reports_no_file(self, monkeypatch, in_terminal):
        monkeypatch.setattr(
            cc,
            "_run_wsh",
            make_wsh({"getmeta": (0, "null\n", "")}),
        )
        result = json.loads(cc.crowe_code_read_block(UUID))
        assert result["backed_by_file"] is False
        assert "scratch" in result["message"].lower()

    def test_rejects_bad_uuid(self, in_terminal):
        result = json.loads(cc.crowe_code_read_block("not a real id !!"))
        assert "error" in result

    def test_requires_terminal(self, monkeypatch):
        monkeypatch.setattr(cc, "in_crowe_terminal", lambda: False)
        result = json.loads(cc.crowe_code_read_block(UUID))
        assert "error" in result
        assert "Crowe Terminal" in result["error"]

    def test_surfaces_wsh_failure(self, monkeypatch, in_terminal):
        monkeypatch.setattr(
            cc,
            "_run_wsh",
            make_wsh({"getmeta": (1, "", "getting metadata: block not found")}),
        )
        result = json.loads(cc.crowe_code_read_block(UUID))
        assert "error" in result


# ---------------------------------------------------------------------------
# write_block
# ---------------------------------------------------------------------------


class TestWriteBlock:
    def test_writes_to_existing_file(self, monkeypatch, in_terminal, tmp_path):
        f = tmp_path / "buf.py"
        f.write_text("old\n")
        monkeypatch.setattr(
            cc,
            "_run_wsh",
            make_wsh({"getmeta": (0, str(f) + "\n", "")}),
        )
        result = json.loads(cc.crowe_code_write_block(UUID, content="new code\n"))
        assert result["bound_new_file"] is False
        assert f.read_text() == "new code\n"

    def test_autobinds_when_no_file(self, monkeypatch, in_terminal, tmp_path):
        target = tmp_path / "bound.py"
        calls = []

        def getmeta(args):
            return (0, "null\n", "")

        def setmeta(args):
            calls.append(args)
            return (0, "", "")

        monkeypatch.setattr(
            cc,
            "_run_wsh",
            make_wsh({"getmeta": getmeta, "setmeta": setmeta}),
        )
        result = json.loads(
            cc.crowe_code_write_block(UUID, content="x=1\n", file_path=str(target))
        )
        assert result["bound_new_file"] is True
        assert target.read_text() == "x=1\n"
        # setmeta must have been called binding crowecode:file=<target>
        assert any(
            "setmeta" in a[0] and f"crowecode:file={target}" in " ".join(a)
            for a in calls
        )

    def test_autobinds_to_default_dir_when_no_path(
        self, monkeypatch, in_terminal, tmp_path
    ):
        monkeypatch.setattr(cc, "BUFFER_DIR", tmp_path / "buffers")
        monkeypatch.setattr(
            cc,
            "_run_wsh",
            make_wsh({"getmeta": (0, "null\n", ""), "setmeta": (0, "", "")}),
        )
        result = json.loads(cc.crowe_code_write_block(UUID, content="hi\n"))
        written = Path(result["file"])
        assert written.exists()
        assert UUID in str(written)

    def test_accepts_content_b64(self, monkeypatch, in_terminal, tmp_path):
        import base64

        f = tmp_path / "b.py"
        f.write_text("old\n")
        monkeypatch.setattr(
            cc,
            "_run_wsh",
            make_wsh({"getmeta": (0, str(f) + "\n", "")}),
        )
        payload = base64.b64encode("from b64\n".encode()).decode()
        cc.crowe_code_write_block(UUID, content_b64=payload)
        assert f.read_text() == "from b64\n"


# ---------------------------------------------------------------------------
# run_block
# ---------------------------------------------------------------------------


class TestRunBlock:
    def test_dispatches_python(self, monkeypatch, in_terminal, tmp_path):
        f = tmp_path / "script.py"
        f.write_text("print(1)\n")
        captured = {}

        def fake_exec(command, working_directory="", timeout_seconds=120):
            captured["command"] = command
            captured["cwd"] = working_directory
            return json.dumps({"stdout": "1\n", "stderr": "", "return_code": 0})

        monkeypatch.setattr(
            cc,
            "_run_wsh",
            make_wsh({"getmeta": (0, str(f) + "\n", "")}),
        )
        monkeypatch.setattr(cc, "execute_shell", fake_exec)
        result = json.loads(cc.crowe_code_run_block(UUID))
        assert "python3" in captured["command"]
        assert str(f) in captured["command"]
        assert result["result"]["return_code"] == 0

    def test_scratch_buffer_cannot_run(self, monkeypatch, in_terminal):
        monkeypatch.setattr(
            cc,
            "_run_wsh",
            make_wsh({"getmeta": (0, "null\n", "")}),
        )
        result = json.loads(cc.crowe_code_run_block(UUID))
        assert "error" in result
        assert "no backing file" in result["error"].lower()

    def test_unknown_extension_requires_interpreter(
        self, monkeypatch, in_terminal, tmp_path
    ):
        f = tmp_path / "thing.xyz"
        f.write_text("data\n")
        monkeypatch.setattr(
            cc,
            "_run_wsh",
            make_wsh({"getmeta": (0, str(f) + "\n", "")}),
        )
        result = json.loads(cc.crowe_code_run_block(UUID))
        assert "error" in result
        assert "interpreter" in result["error"].lower()

    def test_explicit_interpreter_overrides(self, monkeypatch, in_terminal, tmp_path):
        f = tmp_path / "thing.xyz"
        f.write_text("data\n")
        captured = {}

        def fake_exec(command, working_directory="", timeout_seconds=120):
            captured["command"] = command
            return json.dumps({"stdout": "", "stderr": "", "return_code": 0})

        monkeypatch.setattr(
            cc,
            "_run_wsh",
            make_wsh({"getmeta": (0, str(f) + "\n", "")}),
        )
        monkeypatch.setattr(cc, "execute_shell", fake_exec)
        cc.crowe_code_run_block(UUID, interpreter="cat")
        assert captured["command"].startswith("cat ")


# ---------------------------------------------------------------------------
# list_blocks
# ---------------------------------------------------------------------------


class TestListBlocks:
    def test_filters_crowecode_only(self, monkeypatch, in_terminal):
        blocks = [
            {
                "blockid": "term-1",
                "view": "term",
                "tabid": "t",
                "workspaceid": "w",
                "meta": {},
            },
            {
                "blockid": UUID,
                "view": "crowecode",
                "tabid": "t",
                "workspaceid": "w",
                "meta": {"crowecode:file": "/x/wf.py", "crowecode:language": "python"},
            },
        ]
        monkeypatch.setattr(
            cc,
            "_run_wsh",
            make_wsh({"blocks": (0, json.dumps(blocks), "")}),
        )
        result = json.loads(cc.crowe_code_list_blocks())
        assert result["count"] == 1
        only = result["blocks"][0]
        assert only["block"] == UUID
        assert only["file"] == "/x/wf.py"
        assert only["language"] == "python"


# ---------------------------------------------------------------------------
# Registration gate
# ---------------------------------------------------------------------------


class TestRegister:
    def test_adds_tools_when_in_terminal(self, monkeypatch):
        monkeypatch.setattr(cc, "in_crowe_terminal", lambda: True)
        monkeypatch.setattr(cc, "_crowe_code_capable", lambda: True)
        target: set = set()
        added = cc.register(target)
        assert cc.crowe_code_read_block in target
        assert cc.crowe_code_run_block in target
        assert "crowe_code_read_block" in added

    def test_noop_when_not_in_terminal(self, monkeypatch):
        monkeypatch.setattr(cc, "in_crowe_terminal", lambda: False)
        target: set = set()
        added = cc.register(target)
        assert target == set()
        assert added == []


# ---------------------------------------------------------------------------
# Wiring into the agent (system prompt + package registration)
# ---------------------------------------------------------------------------


class TestWiring:
    def test_runtime_instructions_include_addendum_in_terminal(self, monkeypatch):
        import cli.session_runtime as sr

        monkeypatch.setattr(cc, "in_crowe_terminal", lambda: True)
        monkeypatch.setattr(cc, "_crowe_code_capable", lambda: True)
        text = sr.build_runtime_system_instructions()
        assert "Crowe Code blocks" in text

    def test_runtime_instructions_omit_addendum_outside_terminal(self, monkeypatch):
        import cli.session_runtime as sr

        monkeypatch.setattr(cc, "in_crowe_terminal", lambda: False)
        text = sr.build_runtime_system_instructions()
        assert "Crowe Code blocks" not in text

    def test_package_imports_crowe_code(self):
        import tools

        assert hasattr(tools, "crowe_code")


# ---------------------------------------------------------------------------
# Hardening (from adversarial review)
# ---------------------------------------------------------------------------


class TestHardening:
    def test_run_wsh_translates_timeout_to_wsh_error(self, monkeypatch):
        import subprocess

        def boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="wsh", timeout=10)

        monkeypatch.setattr(cc, "_wsh_path", lambda: "/usr/bin/wsh")
        monkeypatch.setattr(cc.subprocess, "run", boom)
        with pytest.raises(cc._WshError):
            cc._run_wsh(["getmeta", "-b", UUID])

    def test_run_wsh_translates_oserror_to_wsh_error(self, monkeypatch):
        def boom(*a, **k):
            raise FileNotFoundError("wsh vanished")

        monkeypatch.setattr(cc, "_wsh_path", lambda: "/usr/bin/wsh")
        monkeypatch.setattr(cc.subprocess, "run", boom)
        with pytest.raises(cc._WshError):
            cc._run_wsh(["getmeta", "-b", UUID])

    def test_read_block_degrades_cleanly_on_timeout(self, monkeypatch, in_terminal):
        def raise_wsh(args, timeout=10):
            raise cc._WshError("wsh timed out after 10s")

        monkeypatch.setattr(cc, "_run_wsh", raise_wsh)
        result = json.loads(cc.crowe_code_read_block(UUID))
        assert "error" in result  # JSON error, not an uncaught traceback

    def test_getmeta_unwraps_quoted_path(self, monkeypatch):
        monkeypatch.setattr(
            cc,
            "_run_wsh",
            make_wsh({"getmeta": (0, '"/tmp/my script.py"\n', "")}),
        )
        assert cc._getmeta_value(UUID, "crowecode:file") == "/tmp/my script.py"

    def test_run_block_does_not_inject_via_args(
        self, monkeypatch, in_terminal, tmp_path
    ):
        import shlex

        f = tmp_path / "script.py"
        f.write_text("print(1)\n")
        captured = {}

        def fake_exec(command, working_directory="", timeout_seconds=120):
            captured["command"] = command
            return json.dumps({"stdout": "", "stderr": "", "return_code": 0})

        monkeypatch.setattr(
            cc, "_run_wsh", make_wsh({"getmeta": (0, str(f) + "\n", "")})
        )
        monkeypatch.setattr(cc, "execute_shell", fake_exec)
        cc.crowe_code_run_block(UUID, args="; echo PWNED")
        # Every token must be individually shell-quoted, so the ';' reaches the
        # script as a literal argv token instead of acting as a shell separator.
        # The raw-concatenation (vulnerable) form would leave a bare " ; " here.
        expected = " ".join(
            shlex.quote(t) for t in ["python3", str(f), ";", "echo", "PWNED"]
        )
        assert captured["command"] == expected
        assert " ; " not in captured["command"]

    def test_run_block_rejects_unbalanced_args(
        self, monkeypatch, in_terminal, tmp_path
    ):
        f = tmp_path / "script.py"
        f.write_text("print(1)\n")
        monkeypatch.setattr(
            cc, "_run_wsh", make_wsh({"getmeta": (0, str(f) + "\n", "")})
        )
        result = json.loads(cc.crowe_code_run_block(UUID, args='"unbalanced'))
        assert "error" in result


# ---------------------------------------------------------------------------
# Phase 2: current-block resolution ("the block I'm looking at")
# ---------------------------------------------------------------------------


class TestCurrentBlock:
    def test_uses_active_editor_env_handshake(self, monkeypatch, in_terminal):
        monkeypatch.setenv("CROWE_CODE_ACTIVE_BLOCK", UUID)
        result = json.loads(cc.crowe_code_current_block())
        assert result["block"] == UUID
        assert result["source"] == "active-editor"

    def test_picks_single_block_in_current_tab(self, monkeypatch, in_terminal):
        monkeypatch.delenv("CROWE_CODE_ACTIVE_BLOCK", raising=False)
        monkeypatch.setenv("WAVETERM_TABID", "tab-1")
        blocks = [
            {"blockid": "other", "view": "term", "tabid": "tab-1", "meta": {}},
            {
                "blockid": UUID,
                "view": "crowecode",
                "tabid": "tab-1",
                "meta": {"crowecode:file": "/x/wf.py"},
            },
            {"blockid": "cc2", "view": "crowecode", "tabid": "tab-9", "meta": {}},
        ]
        monkeypatch.setattr(
            cc, "_run_wsh", make_wsh({"blocks": (0, json.dumps(blocks), "")})
        )
        result = json.loads(cc.crowe_code_current_block())
        assert result["block"] == UUID
        assert result["source"] == "current-tab"
        assert result["file"] == "/x/wf.py"

    def test_falls_back_to_only_open_block(self, monkeypatch, in_terminal):
        monkeypatch.delenv("CROWE_CODE_ACTIVE_BLOCK", raising=False)
        monkeypatch.setenv("WAVETERM_TABID", "tab-7")  # no crowecode in this tab
        blocks = [{"blockid": UUID, "view": "crowecode", "tabid": "tab-3", "meta": {}}]
        monkeypatch.setattr(
            cc, "_run_wsh", make_wsh({"blocks": (0, json.dumps(blocks), "")})
        )
        result = json.loads(cc.crowe_code_current_block())
        assert result["block"] == UUID
        assert result["source"] == "only-open"

    def test_ambiguous_multiple_in_tab_asks(self, monkeypatch, in_terminal):
        monkeypatch.delenv("CROWE_CODE_ACTIVE_BLOCK", raising=False)
        monkeypatch.setenv("WAVETERM_TABID", "tab-1")
        blocks = [
            {"blockid": "a", "view": "crowecode", "tabid": "tab-1", "meta": {}},
            {"blockid": "b", "view": "crowecode", "tabid": "tab-1", "meta": {}},
        ]
        monkeypatch.setattr(
            cc, "_run_wsh", make_wsh({"blocks": (0, json.dumps(blocks), "")})
        )
        result = json.loads(cc.crowe_code_current_block())
        assert "error" in result
        assert result["count"] == 2
        assert len(result["candidates"]) == 2

    def test_none_open(self, monkeypatch, in_terminal):
        monkeypatch.delenv("CROWE_CODE_ACTIVE_BLOCK", raising=False)
        monkeypatch.setattr(cc, "_run_wsh", make_wsh({"blocks": (0, "[]", "")}))
        result = json.loads(cc.crowe_code_current_block())
        assert "error" in result
        assert result["count"] == 0

    def test_degrades_on_no_workspaces(self, monkeypatch, in_terminal):
        monkeypatch.delenv("CROWE_CODE_ACTIVE_BLOCK", raising=False)
        monkeypatch.setattr(
            cc,
            "_run_wsh",
            make_wsh({"blocks": (1, "", "Error: no workspaces found")}),
        )
        result = json.loads(cc.crowe_code_current_block())
        assert "error" in result

    def test_registered_and_in_tool_names(self, monkeypatch):
        monkeypatch.setattr(cc, "in_crowe_terminal", lambda: True)
        monkeypatch.setattr(cc, "_crowe_code_capable", lambda: True)
        target: set = set()
        cc.register(target)
        assert cc.crowe_code_current_block in target
        assert "crowe_code_current_block" in cc._TOOL_NAMES


# ---------------------------------------------------------------------------
# Capability gate — distinguish a Crowe-Code-capable runtime from stock Wave
# ---------------------------------------------------------------------------


class TestCapabilityGate:
    """The cheap JWT+wsh check is necessary but NOT sufficient: stock Wave
    Terminal sets identical WAVETERM_* env and ships the same wsh, yet cannot
    serve Crowe Code block work. `_crowe_code_capable()` probes wsh once to
    tell the two apart, and register()/system_prompt() must honor it.
    """

    @pytest.fixture(autouse=True)
    def _reset_cache(self):
        cc._CAPABILITY_CACHE = None
        yield
        cc._CAPABILITY_CACHE = None

    def test_capable_when_blocks_list_returns_json(self, monkeypatch):
        monkeypatch.setattr(cc, "_run_wsh", make_wsh({"blocks": (0, "[]", "")}))
        assert cc._crowe_code_capable() is True

    def test_incapable_when_no_workspaces(self, monkeypatch):
        monkeypatch.setattr(
            cc,
            "_run_wsh",
            make_wsh({"blocks": (1, "", "Error: no workspaces found")}),
        )
        assert cc._crowe_code_capable() is False

    def test_incapable_when_output_not_json(self, monkeypatch):
        monkeypatch.setattr(cc, "_run_wsh", make_wsh({"blocks": (0, "not json", "")}))
        assert cc._crowe_code_capable() is False

    def test_fail_closed_on_wsh_error(self, monkeypatch):
        def boom(args, timeout=10):
            raise cc._WshError("wsh exploded")

        monkeypatch.setattr(cc, "_run_wsh", boom)
        assert cc._crowe_code_capable() is False

    def test_probe_is_cached(self, monkeypatch):
        calls = {"n": 0}

        def counting(args, timeout=10):
            calls["n"] += 1
            return (0, "[]", "")

        monkeypatch.setattr(cc, "_run_wsh", counting)
        cc._crowe_code_capable()
        cc._crowe_code_capable()
        assert calls["n"] == 1

    def test_register_noop_when_incapable(self, monkeypatch):
        monkeypatch.setattr(cc, "in_crowe_terminal", lambda: True)
        monkeypatch.setattr(cc, "_crowe_code_capable", lambda: False)
        target: set = set()
        assert cc.register(target) == []
        assert target == set()

    def test_system_prompt_empty_when_incapable(self, monkeypatch):
        monkeypatch.setattr(cc, "in_crowe_terminal", lambda: True)
        monkeypatch.setattr(cc, "_crowe_code_capable", lambda: False)
        assert cc.system_prompt() == ""
