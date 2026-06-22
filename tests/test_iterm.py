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
    @patch("iterm._write_dynamic_profile")
    @patch("iterm._write_daemon_with_shebang")
    @patch("iterm.os.chmod")
    @patch("iterm.os.makedirs")
    @patch("iterm.os.path.exists", return_value=False)
    @patch("iterm.subprocess.run")
    def test_install_creates_venv_and_copies_daemon(
        self,
        mock_run,
        mock_exists,
        mock_makedirs,
        mock_chmod,
        mock_write_daemon,
        mock_profile,
    ):
        from iterm import install_iterm

        success, msg = install_iterm()
        assert success is True
        assert "Installed" in msg
        venv_calls = [c for c in mock_run.call_args_list if "venv" in str(c)]
        assert len(venv_calls) >= 1
        mock_profile.assert_called_once()


class TestItermUninstall(unittest.TestCase):
    """Test uninstall logic."""

    @patch("iterm.os.path.exists", return_value=True)
    @patch("iterm.os.remove")
    @patch("iterm.shutil.rmtree")
    def test_uninstall_removes_daemon_and_venv(
        self, mock_rmtree, mock_remove, mock_exists
    ):
        from iterm import uninstall_iterm

        success, msg = uninstall_iterm()
        assert success is True
        assert "Uninstalled" in msg
        # daemon + dynamic profile + watermark asset
        assert mock_remove.called

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
    @patch("iterm._is_python_api_enabled", return_value=True)
    def test_status_all_installed(self, mock_api, mock_exists):
        from iterm import iterm_status

        status = iterm_status()
        assert status["iterm_detected"] is True
        assert status["daemon_installed"] is True
        assert status["python_api_enabled"] is True

    @patch.dict(os.environ, {"TERM_PROGRAM": "Apple_Terminal"})
    @patch("iterm._is_python_api_enabled", return_value=False)
    def test_status_not_iterm(self, mock_api):
        from iterm import iterm_status

        status = iterm_status()
        assert status["iterm_detected"] is False
        assert status["python_api_enabled"] is False


class TestItermProfile(unittest.TestCase):
    """Test the Crowe Logic dynamic-profile generation."""

    def test_profile_has_identity_and_branding(self):
        from iterm import build_profile_dict, PROFILE_FONT, PROFILE_GUID, PROFILE_NAME

        p = build_profile_dict()
        assert p["Name"] == PROFILE_NAME
        assert p["Guid"] == PROFILE_GUID
        assert p["Badge Text"] == "CROWE LOGIC"
        assert p["Show Status Bar"] is True
        assert p["Normal Font"] == PROFILE_FONT
        assert p["Use Ligatures"] is True
        assert p["Cursor Type"] == 1
        assert p["Blinking Cursor"] is True
        assert p["Show Cursor Guide"] is True

    def test_profile_colors_are_srgb_with_components(self):
        from iterm import build_profile_dict

        p = build_profile_dict()
        for key in ("Background Color", "Foreground Color", "Badge Color"):
            c = p[key]
            assert c["Color Space"] == "sRGB"
            assert "Red Component" in c and "Blue Component" in c
        # Badge stays legible: higher alpha than the faint watermark default.
        assert p["Badge Color"]["Alpha Component"] == 0.55

    def test_profile_serializes_to_json(self):
        import json
        from iterm import build_profile_dict

        payload = {"Profiles": [build_profile_dict()]}
        # Round-trips cleanly -> iTerm2 can parse it.
        assert json.loads(json.dumps(payload))["Profiles"][0]["Name"] == "Crowe Logic"

    def test_write_dynamic_profile_writes_valid_file(self):
        import json
        import tempfile
        from unittest.mock import patch
        import iterm

        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, "DynamicProfiles", "crowe-logic.json")
            with (
                patch.object(iterm, "DYNAMIC_PROFILES_DIR", os.path.dirname(dest)),
                patch.object(iterm, "DYNAMIC_PROFILE_DEST", dest),
                patch.object(iterm, "WATERMARK_SOURCE", "/nonexistent.png"),
                patch.object(iterm, "WATERMARK_DEST", os.path.join(d, "wm.png")),
            ):
                iterm._write_dynamic_profile()
                data = json.load(open(dest))
        assert data["Profiles"][0]["Guid"] == iterm.PROFILE_GUID
        # Background image omitted gracefully when the asset is absent.
        assert "Background Image Location" not in data["Profiles"][0]

    def test_apply_terminal_chrome_emits_cursor_escape_on_tty(self):
        from unittest.mock import patch
        from iterm import apply_terminal_chrome

        buf = io.StringIO()
        buf.isatty = lambda: True
        with patch("sys.stdout", buf), patch.dict(os.environ, {}, clear=True):
            apply_terminal_chrome()

        output = buf.getvalue()
        assert "\033]12;#bfa669\a" in output
        assert "\033[5 q" in output

    def test_apply_terminal_chrome_can_be_disabled(self):
        from unittest.mock import patch
        from iterm import apply_terminal_chrome

        buf = io.StringIO()
        buf.isatty = lambda: True
        with patch("sys.stdout", buf), patch.dict(
            os.environ, {"CROWE_LOGIC_TERMINAL_CHROME": "0"}, clear=True
        ):
            apply_terminal_chrome()

        assert buf.getvalue() == ""


if __name__ == "__main__":
    unittest.main()
