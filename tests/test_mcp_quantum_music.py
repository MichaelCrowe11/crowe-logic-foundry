"""Tests for the IBM Quantum music MCP server."""

import json

import pytest

# The MCP server script imports the optional `mcp` SDK. Skip the
# whole module when it isn't installed.
pytest.importorskip("mcp.server.fastmcp")


class TestQuantumMusicMcpServer:
    def test_server_module_imports(self):
        """Verify the quantum music server module is importable."""
        import scripts.mcp_quantum_music as mcp_mod

        assert hasattr(mcp_mod, "mcp")

    def test_server_registers_music_tools(self):
        """Verify the music composition tools are exposed via MCP."""
        import scripts.mcp_quantum_music as mcp_mod

        tool_names = [tool.name for tool in mcp_mod.mcp._tool_manager.list_tools()]
        assert "quantum_music_status" in tool_names
        assert "compose_quantum_melody" in tool_names
        assert "compose_quantum_progression" in tool_names
        assert "sonify_quantum_circuit" in tool_names

    def test_melody_tool_uses_sampled_counts(self, monkeypatch):
        """Verify melody generation maps sampled counts into note events."""
        import scripts.mcp_quantum_music as mcp_mod

        monkeypatch.setattr(
            mcp_mod,
            "_sample_counts",
            lambda circuit, shots, backend: {
                "counts": {"00": 12, "01": 8, "10": 4},
                "backend": "aer_simulator",
                "backend_kind": "simulator",
                "shots": shots,
            },
        )
        monkeypatch.setattr(
            mcp_mod, "_build_seed_circuit", lambda *args, **kwargs: object()
        )

        result = json.loads(
            mcp_mod.compose_quantum_melody(root="D", mode="dorian", steps=5, shots=24)
        )

        assert result["backend"] == "aer_simulator"
        assert len(result["melody"]) == 5
        assert all("note" in event for event in result["melody"])

    def test_status_reports_without_qiskit(self, monkeypatch):
        """Verify the status tool degrades gracefully when Qiskit is unavailable."""
        import scripts.mcp_quantum_music as mcp_mod

        monkeypatch.setattr(
            mcp_mod,
            "_load_qiskit",
            lambda: {"available": False, "error": "missing qiskit"},
        )
        result = json.loads(mcp_mod.quantum_music_status())

        assert result["qiskit_available"] is False
        assert result["default_backend"] == "aer"
