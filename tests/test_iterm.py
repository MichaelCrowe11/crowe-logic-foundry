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


if __name__ == "__main__":
    unittest.main()
