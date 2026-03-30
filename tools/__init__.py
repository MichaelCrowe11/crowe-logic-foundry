"""
Crowe Logic Agent — Complete Tool Suite

All functions are registered as FunctionTool with Azure AI Agent Service.
The agent auto-selects and executes them based on the conversation.
"""

# Core tools (filesystem, shell, web)
from tools.filesystem import read_file, write_file, edit_file, list_directory
from tools.shell import execute_shell
from tools.search import web_search, grep_search
from tools.browser import browse_url

# macOS automation
from tools.applescript import run_applescript, open_application, send_notification

# Git operations
from tools.git_ops import git_status, git_diff, git_log, git_commit, git_clone

# Browser automation (Playwright)
from tools.playwright_browser import (
    browser_navigate, browser_click, browser_type_text,
    browser_snapshot, browser_screenshot,
)

# Talon Music Engine
from tools.talon_music import (
    talon_generate_chords, talon_generate_drums, talon_generate_melody,
    talon_quantum_melody, talon_quantum_chord, talon_compose_emotion,
    talon_full_composition, talon_import_midi, talon_analyze,
    talon_list_grooves, talon_list_emotions,
)

# Quantum computing
from tools.quantum import run_quantum_circuit, synapse_evaluate, qubit_flow_execute

# MCP ecosystem (5,800+ servers on demand)
from tools.mcp_registry import mcp_search
from tools.mcp_client import mcp_list_tools, mcp_call_tool, mcp_stop_server

# All user-facing functions the agent can call
user_functions = {
    # Filesystem
    read_file, write_file, edit_file, list_directory,
    # Shell
    execute_shell,
    # Search
    web_search, grep_search,
    # Web browsing
    browse_url,
    # macOS
    run_applescript, open_application, send_notification,
    # Git
    git_status, git_diff, git_log, git_commit, git_clone,
    # Playwright browser
    browser_navigate, browser_click, browser_type_text,
    browser_snapshot, browser_screenshot,
    # Talon Music
    talon_generate_chords, talon_generate_drums, talon_generate_melody,
    talon_quantum_melody, talon_quantum_chord, talon_compose_emotion,
    talon_full_composition, talon_import_midi, talon_analyze,
    talon_list_grooves, talon_list_emotions,
    # Quantum
    run_quantum_circuit, synapse_evaluate, qubit_flow_execute,
    # MCP ecosystem
    mcp_search, mcp_list_tools, mcp_call_tool, mcp_stop_server,
}
