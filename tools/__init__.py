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

# Substrate Album Engine
from tools.substrate import (
    substrate_list_tracks, substrate_render_track, substrate_render_album,
    substrate_render_status, substrate_vocal_status, substrate_mix_vocals,
    substrate_open_track, substrate_dna,
)

# Quantum computing
from tools.quantum import run_quantum_circuit, synapse_evaluate, qubit_flow_execute, trinity_pipeline

# Vision
from tools.vision import analyze_image, screenshot_and_analyze
from tools.video_generation import sora_generate_video

# Studio capture (iPhone / webcam / screen via AVFoundation)
from tools.capture import (
    list_capture_devices, find_iphone_device,
    capture_clip, capture_still,
    start_live_capture, stop_live_capture, list_live_captures,
    get_session_chunks,
    preview_device, stop_preview, enable_center_stage,
)

# Studio routing — drops clips into any tenant pipeline registered in
# config/studio_tenants.yaml (toxicteetv, southwest-mushrooms, etc).
from tools.studio_route import (
    list_tenants, get_tenant, route_clip_to_tenant, tenant_inbox_peek,
)

# Presentation — script ingestion, teleprompter, zoom effects, chapter splits
from tools.presentation import (
    load_script, launch_teleprompter,
    list_zoom_effects, apply_zoom_effect,
    split_recording_by_chapters,
)

# Control Center — single-window FastAPI dashboard that wraps all studio tools
from tools.control_center import start_control_center

# Shoot — multi-camera orchestration (camera registry + start_shoot/stop_shoot)
from tools.shoot import (
    list_cameras, get_camera,
    start_shoot, stop_shoot, list_shoots,
    register_cloud_camera,
)

# Shot selector — script + shoot manifest -> EDL (edit decision list)
from tools.shot_selector import build_edl, load_edl, list_edls

# EDL renderer — EDL -> final multi-angle cut via ffmpeg
from tools.edl_render import render_edl

# Audio waveform sync — align multi-camera clips
from tools.sync import sync_shoot, get_sync_offsets

# Training store — records shot-selection decisions for future fine-tune
from tools.training_store import (
    record_shot_selection, training_stats, export_finetune_jsonl,
)

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

# Arizona public records
from tools.public_records import (
    maricopa_assessor_search_property,
    maricopa_assessor_search_rental,
    maricopa_assessor_get_parcel_details,
    maricopa_recorder_document_url,
    adre_entity_license_search,
    adre_entity_license_details,
    arizona_apartment_public_records_lookup,
)

# Crowe Logic platform
from tools.crowe_logic_ai import crowe_chat, crowe_vision, crowe_grow_log, crowe_generate_sop

# DeepParallel local reasoning
from tools.deepparallel import deepparallel_query, deepparallel_status

# NemoClaw sandbox (OpenShell isolation on Brev-hosted VM)
from tools.nemoclaw import nemoclaw_shell, nemoclaw_health

# ChatGPT Agents Studio (OpenAI Responses API)
from tools.chatgpt_agent import chatgpt_agent_invoke, chatgpt_agent_health

# Azure AI Foundry Agents (azure-ai-agents SDK)
from tools.azure_agent import azure_agent_list, azure_agent_invoke, azure_agent_create

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
    # Substrate Album
    substrate_list_tracks, substrate_render_track, substrate_render_album,
    substrate_render_status, substrate_vocal_status, substrate_mix_vocals,
    substrate_open_track, substrate_dna,
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
    # Arizona public records
    maricopa_assessor_search_property, maricopa_assessor_search_rental,
    maricopa_assessor_get_parcel_details, maricopa_recorder_document_url,
    adre_entity_license_search, adre_entity_license_details,
    arizona_apartment_public_records_lookup,
    # Crowe Logic platform
    crowe_chat, crowe_vision, crowe_grow_log, crowe_generate_sop,
    # DeepParallel local reasoning
    deepparallel_query, deepparallel_status,
    # NemoClaw sandbox
    nemoclaw_shell, nemoclaw_health,
    # ChatGPT Agents Studio (Responses API)
    chatgpt_agent_invoke, chatgpt_agent_health,
    # Azure AI Foundry Agents
    azure_agent_list, azure_agent_invoke, azure_agent_create,
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
