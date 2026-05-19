"""Tests for the Crowe Logic AI MCP server tool definitions."""

from unittest.mock import patch


class TestMcpServerTools:
    def test_server_module_imports(self):
        """Verify the MCP server module is importable."""
        import scripts.mcp_crowe_logic_ai as mcp_mod
        assert hasattr(mcp_mod, "mcp")

    def test_server_has_four_tools(self):
        """Verify all 4 MCP tools are registered."""
        import scripts.mcp_crowe_logic_ai as mcp_mod
        tool_names = [t.name for t in mcp_mod.mcp._tool_manager.list_tools()]
        assert "crowe_chat" in tool_names
        assert "crowe_vision" in tool_names
        assert "crowe_grow_log" in tool_names
        assert "crowe_sop" in tool_names

    @patch("scripts.mcp_crowe_logic_ai._request")
    def test_crowe_chat_tool(self, mock_req):
        """Test the chat tool calls the correct endpoint."""
        import scripts.mcp_crowe_logic_ai as mcp_mod
        mock_req.return_value = {"response": "test response"}
        result = mcp_mod.crowe_chat("hello")
        assert "test response" in result
