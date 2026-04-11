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
from tools.quantum import run_quantum_circuit, synapse_evaluate, qubit_flow_execute, trinity_pipeline

# Vision
from tools.vision import analyze_image, screenshot_and_analyze
from tools.video_generation import sora_generate_video

# CroweLM training data
from tools.crowelm import (
    crowelm_list_datasets, crowelm_dataset_stats, crowelm_search_examples,
    crowelm_inspect_config,
    crowelm_add_example, crowelm_remove_example, crowelm_export_curated,
    crowelm_prepare_training, crowelm_upload_dataset, crowelm_training_status,
)

# CroweLM pipeline infrastructure
from tools.audit_log import crowelm_audit_log
from tools.staging_pipeline import crowelm_list_staging, crowelm_promote_approved
from tools.agent_runner import crowelm_run_agent

# Crowe Logic platform
from tools.crowe_logic_ai import crowe_chat, crowe_vision, crowe_grow_log, crowe_generate_sop

# MCP ecosystem (5,800+ servers on demand)
from tools.mcp_registry import mcp_search
from tools.mcp_client import mcp_list_tools, mcp_call_tool, mcp_stop_server

# iTerm2 terminal control
from tools.iterm2_control import (
    iterm_create_window, iterm_create_tab, iterm_split_pane,
    iterm_send_text, iterm_read_screen, iterm_inject_output,
    iterm_list_sessions, iterm_focus_session, iterm_set_fullscreen,
    iterm_get_theme, iterm_set_profile_colors, iterm_set_badge,
    iterm_broadcast, iterm_stop_broadcast,
    iterm_alert, iterm_prompt_input,
    iterm_set_variable, iterm_get_variable,
)

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
    run_quantum_circuit, synapse_evaluate, qubit_flow_execute, trinity_pipeline,
    # Vision
    analyze_image, screenshot_and_analyze,
    # Video generation
    sora_generate_video,
    # CroweLM training data
    crowelm_list_datasets, crowelm_dataset_stats, crowelm_search_examples,
    crowelm_inspect_config,
    crowelm_add_example, crowelm_remove_example, crowelm_export_curated,
    crowelm_prepare_training, crowelm_upload_dataset, crowelm_training_status,
    # CroweLM pipeline infrastructure
    crowelm_audit_log, crowelm_list_staging, crowelm_promote_approved, crowelm_run_agent,
    # Crowe Logic platform
    crowe_chat, crowe_vision, crowe_grow_log, crowe_generate_sop,
    # MCP ecosystem
    mcp_search, mcp_list_tools, mcp_call_tool, mcp_stop_server,
    # iTerm2 terminal control
    iterm_create_window, iterm_create_tab, iterm_split_pane,
    iterm_send_text, iterm_read_screen, iterm_inject_output,
    iterm_list_sessions, iterm_focus_session, iterm_set_fullscreen,
    iterm_get_theme, iterm_set_profile_colors, iterm_set_badge,
    iterm_broadcast, iterm_stop_broadcast,
    iterm_alert, iterm_prompt_input,
    iterm_set_variable, iterm_get_variable,
}
