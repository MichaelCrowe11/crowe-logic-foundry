"""
Crowe Logic Agent — Complete Tool Suite

All functions are registered as FunctionTool with Azure AI Agent Service.
The agent auto-selects and executes them based on the conversation.

Registration is guarded: each tool module is imported in isolation via
``_register``. A module that fails to import — almost always a missing optional
third-party dependency (the classic case was ``tenacity`` for
``tools.deepparallel``, or ``qiskit``/``cirq`` for ``tools.quantum``) — logs a
warning and is skipped, so one broken module degrades to "its tools are absent"
instead of taking down the entire 100+ tool registry (which made the agent
silently tool-less). ``user_functions`` is created once and mutated in place so
its ``id()`` stays stable for the schema memoization in ``providers._shared``
and the in-place proxy injection in ``tools.crowe_terminal``.
"""

import importlib
import logging

_log = logging.getLogger("crowe_logic.tools")

# All user-facing functions the agent can call. Built incrementally below; the
# identity of this set must stay stable (see module docstring).
user_functions: set = set()


def _register(module_name: str, *names: str) -> None:
    """Import ``tools.<module_name>`` and add the named callables to ``user_functions``.

    Pass no names to import a module purely for its side effects (some modules
    self-register or run setup at import time). On any import error the module is
    skipped with a warning rather than propagating and breaking the whole suite.
    """
    try:
        mod = importlib.import_module(f"tools.{module_name}")
    except Exception as exc:  # noqa: BLE001 — a bad tool module must never break the rest
        _log.warning(
            "tool module %r unavailable, skipping its tools: %s", module_name, exc
        )
        return
    for name in names:
        fn = getattr(mod, name, None)
        if fn is None:
            _log.warning("tools.%s has no attribute %r; skipping", module_name, name)
            continue
        user_functions.add(fn)


# Core tools (filesystem, shell, web)
_register("filesystem", "read_file", "write_file", "edit_file", "list_directory")
_register("shell", "execute_shell")
_register("search", "web_search", "grep_search")
_register("browser", "browse_url")

# macOS automation
_register("applescript", "run_applescript", "open_application", "send_notification")

# Git operations
_register("git_ops", "git_status", "git_diff", "git_log", "git_commit", "git_clone")

# Browser automation (Playwright)
_register(
    "playwright_browser",
    "browser_navigate",
    "browser_click",
    "browser_type_text",
    "browser_snapshot",
    "browser_screenshot",
)

# Talon Music Engine
_register(
    "talon_music",
    "talon_generate_chords",
    "talon_generate_drums",
    "talon_generate_melody",
    "talon_quantum_melody",
    "talon_quantum_chord",
    "talon_compose_emotion",
    "talon_full_composition",
    "talon_import_midi",
    "talon_analyze",
    "talon_list_grooves",
    "talon_list_emotions",
)

# Substrate Album Engine
_register(
    "substrate",
    "substrate_list_tracks",
    "substrate_render_track",
    "substrate_render_album",
    "substrate_render_status",
    "substrate_vocal_status",
    "substrate_mix_vocals",
    "substrate_open_track",
    "substrate_dna",
)

# Quantum computing (optional deps: qiskit / cirq / pennylane)
_register(
    "quantum",
    "run_quantum_circuit",
    "synapse_evaluate",
    "qubit_flow_execute",
    "trinity_pipeline",
)

# Vision
_register("vision", "analyze_image", "screenshot_and_analyze")
_register("video_generation", "sora_generate_video")

# Studio capture / routing / presentation / control-center / shoot / EDL / sync /
# training-store: imported for their side effects (not part of the default agent
# catalog, but kept loading exactly as before — now guarded).
_register("capture")
_register("studio_route")
_register("presentation")
_register("control_center")
_register("shoot")
_register("shot_selector")
_register("edl_render")
_register("sync")
_register("training_store")

# CroweLM training data
_register(
    "crowelm",
    "crowelm_list_datasets",
    "crowelm_dataset_stats",
    "crowelm_search_examples",
    "crowelm_inspect_config",
    "crowelm_add_example",
    "crowelm_remove_example",
    "crowelm_export_curated",
    "crowelm_prepare_training",
    "crowelm_upload_dataset",
    "crowelm_training_status",
)

# CroweLM pipeline infrastructure
_register("audit_log", "crowelm_audit_log")
_register("staging_pipeline", "crowelm_list_staging", "crowelm_promote_approved")
_register("agent_runner", "crowelm_run_agent")

# Arizona public records
_register(
    "public_records",
    "maricopa_assessor_search_property",
    "maricopa_assessor_search_rental",
    "maricopa_assessor_get_parcel_details",
    "maricopa_recorder_document_url",
    "adre_entity_license_search",
    "adre_entity_license_details",
    "arizona_apartment_public_records_lookup",
)

# Crowe Logic platform
_register(
    "crowe_logic_ai",
    "crowe_chat",
    "crowe_vision",
    "crowe_grow_log",
    "crowe_generate_sop",
)
_register("cultivation_kb", "crowe_knowledge_base")

# DeepParallel local reasoning (optional dep: tenacity)
_register("deepparallel", "deepparallel_query", "deepparallel_status")

# NemoClaw sandbox (OpenShell isolation on Brev-hosted VM)
_register("nemoclaw", "nemoclaw_shell", "nemoclaw_health")

# ChatGPT Agents Studio (OpenAI Responses API)
_register("chatgpt_agent", "chatgpt_agent_invoke", "chatgpt_agent_health")

# Azure AI Foundry Agents (azure-ai-agents SDK)
_register("azure_agent", "azure_agent_list", "azure_agent_invoke", "azure_agent_create")

# MCP ecosystem (5,800+ servers on demand)
_register("mcp_registry", "mcp_search")
_register("mcp_client", "mcp_list_tools", "mcp_call_tool", "mcp_stop_server")

# Crowe Portfolio knowledge plane: registry + agent catalog + dataset catalog
# + code KB hybrid search across the entire Crowe Logic codebase.
# Backed by crowe-portfolio-http (CROWE_PORTFOLIO_URL + CROWE_PORTFOLIO_TOKEN).
_register(
    "portfolio_tools",
    "portfolio_search_code",
    "portfolio_find_canonical",
    "portfolio_list_repos",
    "portfolio_show_repo",
    "portfolio_list_clusters",
    "portfolio_list_agents",
    "portfolio_get_agent",
    "portfolio_list_datasets",
    "portfolio_stale_repos",
)

# iTerm2 terminal control
_register(
    "iterm2_control",
    "iterm_create_window",
    "iterm_create_tab",
    "iterm_split_pane",
    "iterm_send_text",
    "iterm_read_screen",
    "iterm_inject_output",
    "iterm_list_sessions",
    "iterm_focus_session",
    "iterm_set_fullscreen",
    "iterm_get_theme",
    "iterm_set_profile_colors",
    "iterm_set_badge",
    "iterm_broadcast",
    "iterm_stop_broadcast",
    "iterm_alert",
    "iterm_prompt_input",
    "iterm_set_variable",
    "iterm_get_variable",
)

# Crowe Terminal control plane (active when CROWE_AGENT_TOOLS=1 and the
# terminal is running). Imported AFTER user_functions is populated so the
# discover_and_register call at import time can mutate the set in place.
# Silent no-op when the terminal isn't reachable.
try:
    from tools import crowe_terminal as _crowe_terminal  # noqa: F401, E402
except Exception as exc:  # noqa: BLE001
    _log.warning("crowe_terminal proxy unavailable: %s", exc)

# Crowe Code editor-block tools (crowecode blocks). Registered only when
# crowe-logic runs inside a Crowe Terminal block (WAVETERM_JWT + wsh reachable);
# a silent no-op elsewhere, so the tools never surface where they can't work.
try:
    from tools import crowe_code as _crowe_code  # noqa: E402

    _crowe_code.register(user_functions)
except Exception as exc:  # noqa: BLE001
    _log.warning("crowe_code tools unavailable: %s", exc)
