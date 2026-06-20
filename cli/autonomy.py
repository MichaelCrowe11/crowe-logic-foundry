"""Autonomy gate — a graduated tool-permission tier for the agent.

A single, legible knob that decides *which* of the registered tools the agent
may use this turn, by capability:

    read_only  →  only pure-read tools (inspect, search, fetch). No writes,
                  no shell, no state change. This is the substrate of
                  Specification Mode: the agent can study the codebase but
                  cannot touch it.
    edit       →  read_only + file edits (write/edit/create files).
    execute    →  edit + shell, git, browser, sends, and domain/unknown tools.
    full       →  no restriction (the historical default).

The tiers are cumulative (read_only ⊂ edit ⊂ execute ⊂ full) and the classifier
is FAIL-CLOSED: any tool not explicitly known to be a safe read is treated as
capable of acting, so it never leaks into read_only / spec mode.

This is provider-agnostic pure logic: it filters a ``{name: callable}`` map.
The CLI wires it into _get_tool_map() (execution gate) and the tool schema.
"""

from __future__ import annotations

AUTONOMY_LEVELS = ("read_only", "edit", "execute", "full")
DEFAULT_LEVEL = "full"

# ---------------------------------------------------------------------------
# >>> LEARNING-MODE CONTRIBUTION POINT <<<
# Which of the ~218 registered tools count as *safe pure reads* is domain
# judgment, and your knowledge of the toolset improves it. This allow-list is
# deliberately conservative: anything not here is treated as able to act, so
# read-only / spec mode can never expose it. Widen it as you vet more read
# tools (e.g. additional portfolio_* / public-records lookups, mcp_list_*).
# ---------------------------------------------------------------------------
READ_TOOLS = frozenset(
    {
        "read_file",
        "list_directory",
        "grep_search",
        "web_search",
        "browse_url",
        "git_status",
        "git_diff",
        "git_log",
        "analyze_image",
        "screenshot_and_analyze",
        "portfolio_search_code",
        "portfolio_find_canonical",
        "portfolio_list_repos",
        "portfolio_list_agents",
        "portfolio_list_datasets",
        "crowe_knowledge_base",
        "crowelm_list_datasets",
        "crowelm_search_examples",
        "deepparallel_status",
        "mcp_search",
        "mcp_list_tools",
        "maricopa_assessor_search_property",
        "adre_entity_license_search",
    }
)

# File-mutation tools — the one step above pure read.
EDIT_TOOLS = frozenset(
    {
        "write_file",
        "edit_file",
        "create_file",
        "save",
        "Write",
        "Edit",
    }
)

# Capability tier ranks and autonomy-level ranks (cumulative inclusion).
_TIER_RANK = {"read": 0, "edit": 1, "execute": 2}
_LEVEL_RANK = {"read_only": 0, "edit": 1, "execute": 2, "full": 3}


def classify_tool(name: str) -> str:
    """Return a tool's capability tier: ``read`` | ``edit`` | ``execute``.

    Fail-closed: anything not a known read or edit is ``execute`` (assumed able
    to act), so unknown/domain tools never appear in read_only mode.
    """
    if name in READ_TOOLS:
        return "read"
    if name in EDIT_TOOLS:
        return "edit"
    return "execute"


def tool_allowed(name: str, level: str) -> bool:
    """Is ``name`` permitted at autonomy ``level``?"""
    if level == "full":
        return True
    if level not in _LEVEL_RANK:
        raise ValueError(
            f"unknown autonomy level: {level!r} (use one of {AUTONOMY_LEVELS})"
        )
    return _TIER_RANK[classify_tool(name)] <= _LEVEL_RANK[level]


def filter_tools(tool_map: dict, level: str) -> dict:
    """Return the subset of ``tool_map`` allowed at autonomy ``level``."""
    if level == "full":
        return dict(tool_map)
    return {name: fn for name, fn in tool_map.items() if tool_allowed(name, level)}


def filter_functions(functions, level: str) -> list:
    """Filter an iterable of tool callables by autonomy ``level`` (by __name__).

    Used where the model's tool SCHEMA is built from function objects, so a
    restricted level hides forbidden tools from the model entirely (not just at
    execution time).
    """
    if level == "full":
        return list(functions)
    return [fn for fn in functions if tool_allowed(getattr(fn, "__name__", ""), level)]


def filter_schemas(schemas, level: str) -> list:
    """Filter OpenAI-style tool schemas (``{'function': {'name': ...}}``) by level."""
    if level == "full":
        return list(schemas)
    out = []
    for s in schemas:
        name = ""
        if isinstance(s, dict):
            fn = s.get("function")
            name = (
                (fn or {}).get("name", "")
                if isinstance(fn, dict)
                else s.get("name", "")
            )
        if tool_allowed(name, level):
            out.append(s)
    return out


# ---------------------------------------------------------------------------
# Active level — process-wide state both cli/ and providers/ read, so the
# execution gate and the model-facing schema stay in lockstep. Lives here
# (a leaf module that imports nothing) to avoid an import cycle.
# ---------------------------------------------------------------------------
_ACTIVE_LEVEL = DEFAULT_LEVEL


def get_active_level() -> str:
    return _ACTIVE_LEVEL


def set_active_level(level: str) -> None:
    global _ACTIVE_LEVEL
    if level not in AUTONOMY_LEVELS:
        raise ValueError(
            f"unknown autonomy level: {level!r} (use one of {AUTONOMY_LEVELS})"
        )
    _ACTIVE_LEVEL = level


# Planning-phase system prompt for Specification Mode. The autonomy gate makes
# the read-only guarantee real; this prompt makes the model use it well.
SPEC_SYSTEM_PROMPT = (
    "You are CroweLM in Specification Mode. You are operating under a read-only "
    "tool policy: you can inspect the codebase, search, and fetch context, but "
    "you cannot write, edit, run shells, or change any state — and you must not "
    "try.\n\n"
    "Your job is to produce a precise implementation SPEC, not code. Study the "
    "existing patterns and conventions first, then output:\n"
    "  1. Outcome & acceptance criteria (what 'done' means, testably).\n"
    "  2. Technical strategy (the approach, and why, given what the code does).\n"
    "  3. File-by-file change plan (each file: what changes and why).\n"
    "  4. Risks, unknowns, and what to verify.\n\n"
    "Be concrete and grounded in the actual code you inspected. Do not write the "
    "implementation; the human will review and approve this spec first."
)
