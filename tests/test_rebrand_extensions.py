"""
Regression tests for the Crowe Logic Code IDE rebrand.

These exist because we shipped six rounds of theme/install/restart cycles
that all failed for the same root cause: a duplicate command registration
across the two extensions made one of them throw on activate(), which
silently killed theme + walkthrough loading. None of the prior diagnostics
caught it because the contract between the two extensions was not
machine-checked. This file IS that machine check.

Coverage:
- No two extensions register the same command id, command name, or
  chat-participant id.
- Theme JSON files are valid JSON and have parity in key coverage so
  one mode never falls back to VS Code defaults for keys the other has.
- package.json files are valid JSON.
- No em dashes appear in user-visible strings (descriptions, titles,
  walkthrough copy, default settings values).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
CHAT_EXT = REPO_ROOT / "deploy" / "ide" / "extensions" / "crowe-logic"
REBRAND_EXT = REPO_ROOT / "vscode" / "extension"

# User-visible package.json keys whose values render in the IDE chrome.
# Internal _comment fields and CSS comments inside .ts files are not
# user-visible and stay out of scope here.
USER_VISIBLE_KEY_HINTS = (
    "title",
    "displayName",
    "description",
    "fullName",
    "label",
    "tooltip",
    "detail",
    "altText",
    "default",     # default values for configurable string settings
)

EM_DASH = "—"


# ─── Loaders ─────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def chat_pkg() -> dict:
    return json.loads((CHAT_EXT / "package.json").read_text())


@pytest.fixture(scope="module")
def rebrand_pkg() -> dict:
    return json.loads((REBRAND_EXT / "package.json").read_text())


# ─── JSON validity ───────────────────────────────────────────────────


def test_chat_extension_package_json_valid(chat_pkg):
    assert chat_pkg["name"] == "crowe-logic"
    assert chat_pkg["publisher"] == "crowe-logic"


def test_rebrand_extension_package_json_valid(rebrand_pkg):
    assert rebrand_pkg["name"] == "crowe-logic-vscode"
    assert rebrand_pkg["publisher"] == "crowe-logic"


def test_theme_jsons_are_valid():
    for theme in (REBRAND_EXT / "themes").glob("*.json"):
        json.loads(theme.read_text())


# ─── Duplicate registration regression (the launch blocker) ──────────


def _commands(pkg: dict) -> set[str]:
    contributes = pkg.get("contributes") or {}
    return {c["command"] for c in contributes.get("commands") or []}


def _participant_ids(pkg: dict) -> set[str]:
    contributes = pkg.get("contributes") or {}
    return {p["id"] for p in contributes.get("chatParticipants") or []}


def _participant_names(pkg: dict) -> set[str]:
    contributes = pkg.get("contributes") or {}
    return {p["name"] for p in contributes.get("chatParticipants") or []}


def test_no_duplicate_commands_between_extensions(chat_pkg, rebrand_pkg):
    """The original launch blocker: both extensions registering the same
    command id made one of them throw on activate(), which killed themes
    and the walkthrough. Adding a contribution that overlaps must fail
    this test before it ships."""
    overlap = _commands(chat_pkg) & _commands(rebrand_pkg)
    assert not overlap, (
        f"Duplicate command registrations across extensions: {sorted(overlap)}. "
        "Pick one extension to own each command. Activation throws when both "
        "register the same id, which silently kills the second extension."
    )


def test_no_duplicate_chat_participant_ids(chat_pkg, rebrand_pkg):
    overlap = _participant_ids(chat_pkg) & _participant_ids(rebrand_pkg)
    assert not overlap, f"Duplicate chat participant ids: {sorted(overlap)}"


def test_no_duplicate_chat_participant_names(chat_pkg, rebrand_pkg):
    overlap = _participant_names(chat_pkg) & _participant_names(rebrand_pkg)
    assert not overlap, (
        f"Duplicate chat participant names: {sorted(overlap)}. "
        "Different ids with the same `name` create ambiguous @mention routing."
    )


def test_chat_extension_also_registers_signin():
    """Anchor: chat extension is the canonical owner of crowe-logic.signIn.
    If this assertion ever fails, the rebrand extension must also stop
    declaring it (the duplicate-command test above will fail otherwise)."""
    chat_pkg = json.loads((CHAT_EXT / "package.json").read_text())
    assert "crowe-logic.signIn" in _commands(chat_pkg)


# ─── Theme parity ────────────────────────────────────────────────────


def _flatten_keys(obj: dict, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for k, v in obj.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            keys |= _flatten_keys(v, path)
        else:
            keys.add(path)
    return keys


def test_theme_dark_and_light_have_parity():
    """The light theme was 43 lines while dark was 175. With the user on
    Crowe Logic Light, the IDE fell back to VS Code defaults for tabs,
    panels, terminal, breadcrumbs, etc. This test prevents that drift
    from coming back: every UI color key in dark must also exist in light,
    and vice versa."""
    dark = json.loads((REBRAND_EXT / "themes" / "crowe-logic-dark.json").read_text())
    light = json.loads((REBRAND_EXT / "themes" / "crowe-logic-light.json").read_text())

    dark_colors = set(dark.get("colors", {}).keys())
    light_colors = set(light.get("colors", {}).keys())

    only_in_dark = dark_colors - light_colors
    only_in_light = light_colors - dark_colors

    assert not only_in_dark and not only_in_light, (
        f"Theme parity broken.\n"
        f"  Only in dark ({len(only_in_dark)}): {sorted(only_in_dark)[:5]}{'...' if len(only_in_dark) > 5 else ''}\n"
        f"  Only in light ({len(only_in_light)}): {sorted(only_in_light)[:5]}{'...' if len(only_in_light) > 5 else ''}"
    )


def test_themes_cover_critical_ui_areas():
    """Floor for theme completeness. If any of these are missing the
    light theme will look broken even if it has the basics."""
    required = {
        "editor.background",
        "editor.foreground",
        "tab.activeBackground",
        "tab.activeForeground",
        "panel.background",
        "terminal.background",
        "terminal.foreground",
        "statusBar.background",
        "activityBar.background",
        "sideBar.background",
        "list.activeSelectionBackground",
        "input.background",
        "dropdown.background",
        "scrollbarSlider.background",
        "menu.background",
        "breadcrumb.foreground",
        "minimap.background",
    }
    for theme_file in (REBRAND_EXT / "themes").glob("*.json"):
        theme = json.loads(theme_file.read_text())
        keys = set(theme.get("colors", {}).keys())
        missing = required - keys
        assert not missing, f"{theme_file.name} missing critical keys: {sorted(missing)}"


# ─── No em dashes in user-visible strings ────────────────────────────


def _walk_user_visible(obj, parents=()):
    """Yield (key_path, value) pairs for every string under a key whose
    last segment hints at user visibility (title, description, label,
    etc.). Skips _comment-style internal keys."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.startswith("_"):
                continue
            yield from _walk_user_visible(v, parents + (k,))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            yield from _walk_user_visible(item, parents + (str(i),))
    elif isinstance(obj, str) and parents:
        last = parents[-1]
        if any(hint in last for hint in USER_VISIBLE_KEY_HINTS):
            yield ".".join(parents), obj


@pytest.mark.parametrize("pkg_path", [
    CHAT_EXT / "package.json",
    REBRAND_EXT / "package.json",
])
def test_no_em_dashes_in_user_visible_strings(pkg_path):
    """The rebrand has a universal style rule against em dashes. Strings
    inside walkthrough descriptions, command titles, configuration
    defaults, and chat participant descriptions all surface in the IDE
    and must not contain U+2014."""
    pkg = json.loads(pkg_path.read_text())
    offenders = [
        (path, value) for path, value in _walk_user_visible(pkg)
        if EM_DASH in value
    ]
    assert not offenders, (
        f"{pkg_path.name} contains em dashes in user-visible strings:\n"
        + "\n".join(f"  {p}: {v!r}" for p, v in offenders)
    )


# ─── Activation events sanity ────────────────────────────────────────


def test_chat_extension_activates_for_lm_lookup(chat_pkg):
    """If our LM provider's vendor matches an activation event prefix,
    VS Code will activate the chat extension when chat needs the LM.
    Without this, the provider never registers and chat falls back to
    the hardcoded copilot-default lookup."""
    events = chat_pkg.get("activationEvents", [])
    assert any(e.startswith("onLanguageModelChat:") for e in events), (
        "chat extension must declare onLanguageModelChat:<vendor> so the "
        "LM provider activates when chat looks for it"
    )


def test_chat_extension_declares_chat_provider_proposal(chat_pkg):
    """vscode.lm.registerLanguageModelChatProvider is a proposed API
    (chatProvider). Without enabledApiProposals, the runtime call falls
    through silently."""
    proposals = chat_pkg.get("enabledApiProposals", [])
    assert "chatProvider" in proposals
