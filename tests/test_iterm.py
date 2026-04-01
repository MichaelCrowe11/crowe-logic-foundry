"""Tests for the iTerm2 integration module."""

import os
import sys
import io
import unittest
from unittest.mock import patch


class TestItermSetVar(unittest.TestCase):
    """Test the iTerm2 user variable escape sequence helper."""

    @patch.dict(os.environ, {"TERM_PROGRAM": "iTerm.app"})
    def test_emits_escape_sequence_in_iterm(self):
        """Should emit the correct escape sequence when running in iTerm2."""
        from iterm import iterm_set_var
        import base64

        buf = io.StringIO()
        with patch("sys.stdout", buf):
            iterm_set_var("crowe_logic_active", "1")

        output = buf.getvalue()
        expected_payload = base64.b64encode(b"crowe_logic_active=1").decode()
        assert f"\033]1337;SetUserVar={expected_payload}\a" == output

    @patch.dict(os.environ, {"TERM_PROGRAM": "Apple_Terminal"})
    def test_noop_on_non_iterm(self):
        """Should emit nothing when not running in iTerm2."""
        from iterm import iterm_set_var

        buf = io.StringIO()
        with patch("sys.stdout", buf):
            iterm_set_var("crowe_logic_active", "1")

        assert buf.getvalue() == ""

    @patch.dict(os.environ, {}, clear=True)
    def test_noop_when_term_program_missing(self):
        """Should emit nothing when TERM_PROGRAM is not set."""
        from iterm import iterm_set_var

        buf = io.StringIO()
        with patch("sys.stdout", buf):
            iterm_set_var("crowe_logic_active", "1")

        assert buf.getvalue() == ""

    @patch.dict(os.environ, {"TERM_PROGRAM": "WezTerm"})
    def test_emits_in_wezterm(self):
        """WezTerm also supports iTerm2 escape sequences."""
        from iterm import iterm_set_var
        import base64

        buf = io.StringIO()
        with patch("sys.stdout", buf):
            iterm_set_var("crowe_logic_tools", "7")

        output = buf.getvalue()
        expected_payload = base64.b64encode(b"crowe_logic_tools=7").decode()
        assert f"\033]1337;SetUserVar={expected_payload}\a" == output


class TestItermPaths(unittest.TestCase):
    """Test path constants and helper functions."""

    def test_daemon_dest_path(self):
        from iterm import DAEMON_DEST
        assert "iTerm2/Scripts/AutoLaunch/crowe-logic-daemon.py" in DAEMON_DEST

    def test_venv_path(self):
        from iterm import ITERM_VENV
        assert ".crowe-logic/iterm-env" in ITERM_VENV

    def test_daemon_source_exists(self):
        from iterm import DAEMON_SOURCE
        assert DAEMON_SOURCE.endswith("daemon.py")


class TestItermInstall(unittest.TestCase):
    """Test install logic (filesystem operations mocked)."""

    @patch.dict(os.environ, {"TERM_PROGRAM": "Apple_Terminal"})
    def test_install_rejects_non_iterm(self):
        from iterm import install_iterm
        success, msg = install_iterm()
        assert success is False
        assert "iTerm2" in msg

    @patch.dict(os.environ, {"TERM_PROGRAM": "iTerm.app"})
    @patch("iterm._write_daemon_with_shebang")
    @patch("iterm.os.chmod")
    @patch("iterm.os.makedirs")
    @patch("iterm.os.path.exists", return_value=False)
    @patch("iterm.subprocess.run")
    def test_install_creates_venv_and_copies_daemon(
        self, mock_run, mock_exists, mock_makedirs, mock_chmod, mock_write_daemon
    ):
        from iterm import install_iterm
        success, msg = install_iterm()
        assert success is True
        assert "Installed" in msg
        venv_calls = [c for c in mock_run.call_args_list if "venv" in str(c)]
        assert len(venv_calls) >= 1


class TestItermUninstall(unittest.TestCase):
    """Test uninstall logic."""

    @patch("iterm.os.path.exists", return_value=True)
    @patch("iterm.os.remove")
    @patch("iterm.shutil.rmtree")
    def test_uninstall_removes_daemon_and_venv(self, mock_rmtree, mock_remove, mock_exists):
        from iterm import uninstall_iterm
        success, msg = uninstall_iterm()
        assert success is True
        assert "Uninstalled" in msg
        mock_remove.assert_called_once()

    @patch("iterm.os.path.exists", return_value=False)
    def test_uninstall_when_not_installed(self, mock_exists):
        from iterm import uninstall_iterm
        success, msg = uninstall_iterm()
        assert success is True
        assert "not installed" in msg.lower() or "Uninstalled" in msg


class TestItermStatus(unittest.TestCase):
    """Test status reporting."""

    @patch.dict(os.environ, {"TERM_PROGRAM": "iTerm.app"})
    @patch("iterm.os.path.exists", return_value=True)
    def test_status_all_installed(self, mock_exists):
        from iterm import iterm_status
        status = iterm_status()
        assert status["iterm_detected"] is True
        assert status["daemon_installed"] is True

    @patch.dict(os.environ, {"TERM_PROGRAM": "Apple_Terminal"})
    def test_status_not_iterm(self):
        from iterm import iterm_status
        status = iterm_status()
        assert status["iterm_detected"] is False


if __name__ == "__main__":
    unittest.main()
