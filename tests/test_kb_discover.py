"""Tests for the auto-discovery layer + overlay persistence.

The discovery logic is exercised with canned portfolio responses so
no HTTP is required. The overlay round-trip writes to tmp_path and
reads back through the same parser.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knowledge_lake.discover import (
    DiscoveredSource,
    discover_sources,
    _count_kinds,
    _pick_kind,
    _parse_listing,
)
from knowledge_lake import sources as sources_mod


# ─── Helpers ──────────────────────────────────────────────────────

def _seed_repo(root: Path, *, md: int = 0, tex: int = 0, jsonl: int = 0) -> None:
    """Create `n` files of each extension inside `root`."""
    root.mkdir(parents=True, exist_ok=True)
    for i in range(md):
        (root / f"doc-{i}.md").write_text(f"# Doc {i}\n\nbody {i}\n")
    for i in range(tex):
        (root / f"chap-{i}.tex").write_text(f"\\section{{Chapter {i}}}\nbody\n")
    for i in range(jsonl):
        (root / f"data-{i}.jsonl").write_text('{"a": 1}\n{"a": 2}\n')


def _portfolio(repos: list[dict] | None = None, datasets: list[dict] | None = None):
    """Return a portfolio_loader callable that yields the given lists."""
    def loader():
        return {
            "repos": list(repos or []),
            "datasets": list(datasets or []),
        }
    return loader


# ─── Filesystem inspection ────────────────────────────────────────

def test_count_kinds_distinguishes_extensions(tmp_path):
    _seed_repo(tmp_path, md=4, tex=2, jsonl=1)
    counts = _count_kinds(tmp_path)
    assert counts["markdown"] == 4
    assert counts["latex"] == 2
    assert counts["jsonl"] == 1


def test_count_kinds_skips_vendored_dirs(tmp_path):
    _seed_repo(tmp_path, md=3)
    # These should all be ignored:
    for skip in (".git", ".venv", "node_modules", "__pycache__"):
        d = tmp_path / skip
        d.mkdir()
        (d / "junk.md").write_text("noise")
    counts = _count_kinds(tmp_path)
    assert counts["markdown"] == 3


def test_pick_kind_enforces_floor():
    # 2 markdown files is below the 3-file floor; nothing chosen.
    assert _pick_kind({"markdown": 2}) is None
    assert _pick_kind({"markdown": 3}) == ("markdown", 3)
    # Tied counts pick deterministically (max returns first hit).
    pick = _pick_kind({"markdown": 5, "latex": 5})
    assert pick is not None and pick[1] == 5


def test_pick_kind_on_empty_returns_none():
    assert _pick_kind({}) is None


# ─── Parser tolerance ─────────────────────────────────────────────

@pytest.mark.parametrize("raw", [
    '[]',
    '{"items": []}',
    '{"repos": []}',
    '{"error": "portfolio_not_provisioned"}',
    'not json at all',
    '',
])
def test_parse_listing_tolerates_garbage(raw):
    assert _parse_listing(raw) == []


def test_parse_listing_unwraps_known_keys():
    raw = json.dumps({"items": [{"name": "a"}, {"name": "b"}]})
    assert _parse_listing(raw) == [{"name": "a"}, {"name": "b"}]
    raw = json.dumps([{"name": "c"}])
    assert _parse_listing(raw) == [{"name": "c"}]


# ─── End-to-end discovery ─────────────────────────────────────────

def test_discover_returns_candidates_sorted_by_count(tmp_path):
    small = tmp_path / "small"
    large = tmp_path / "large"
    _seed_repo(small, md=3)
    _seed_repo(large, md=12)

    candidates = discover_sources(portfolio_loader=_portfolio(repos=[
        {"name": "small", "local_path": str(small)},
        {"name": "large", "local_path": str(large)},
    ]))

    assert [c.name for c in candidates] == ["large", "small"]
    assert candidates[0].file_count == 12
    assert candidates[0].kind == "markdown"


def test_discover_skips_already_registered(tmp_path):
    repo = tmp_path / "foundry-docs"
    _seed_repo(repo, md=10)

    candidates = discover_sources(
        portfolio_loader=_portfolio(repos=[
            {"name": "foundry-docs", "local_path": str(repo)},
        ]),
        already_registered=["foundry-docs"],
    )
    assert candidates == []


def test_discover_skips_entries_without_local_clone(tmp_path):
    # Path that doesn't exist gets silently dropped.
    candidates = discover_sources(portfolio_loader=_portfolio(repos=[
        {"name": "ghost", "local_path": str(tmp_path / "missing")},
    ]))
    assert candidates == []


def test_discover_respects_min_files_floor(tmp_path):
    # 2 files is below the floor; the candidate is suppressed.
    repo = tmp_path / "tiny"
    _seed_repo(repo, md=2)
    candidates = discover_sources(portfolio_loader=_portfolio(repos=[
        {"name": "tiny", "local_path": str(repo)},
    ]))
    assert candidates == []


def test_discover_handles_datasets_with_prefix(tmp_path):
    repo = tmp_path / "ds-foo"
    _seed_repo(repo, jsonl=5)
    candidates = discover_sources(portfolio_loader=_portfolio(datasets=[
        {"name": "foo", "local_path": str(repo)},
    ]))
    assert len(candidates) == 1
    assert candidates[0].name == "dataset-foo"
    assert candidates[0].origin == "portfolio_dataset"
    assert candidates[0].kind == "jsonl"


def test_discover_default_loader_with_missing_creds_returns_empty(monkeypatch):
    # Portfolio creds unset and no HTTP server reachable → empty list,
    # no exception. (We don't need monkeypatch since portfolio_tools
    # already returns the error JSON when env is unset; just verify
    # the wrapper is graceful.)
    monkeypatch.delenv("CROWE_PORTFOLIO_URL", raising=False)
    monkeypatch.delenv("CROWE_PORTFOLIO_TOKEN", raising=False)
    out = discover_sources()
    assert out == []


# ─── Overlay round-trip ───────────────────────────────────────────

def test_overlay_round_trip(tmp_path):
    overlay = tmp_path / "kb-sources.json"
    src = sources_mod.Source(
        name="auto-mycelium",
        kind="markdown",
        root=tmp_path / "mycelium",
        description="discovered",
    )
    sources_mod.save_overlay([src], path=overlay)
    loaded = sources_mod._load_overlay(path=overlay)
    assert len(loaded) == 1
    assert loaded[0].name == "auto-mycelium"
    assert loaded[0].kind == "markdown"


def test_overlay_load_missing_file_returns_empty(tmp_path):
    assert sources_mod._load_overlay(path=tmp_path / "absent.json") == []


def test_overlay_load_malformed_file_returns_empty(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")
    assert sources_mod._load_overlay(path=bad) == []


def test_overlay_load_skips_malformed_entries(tmp_path):
    overlay = tmp_path / "kb-sources.json"
    overlay.write_text(json.dumps({"sources": [
        {"name": "ok", "kind": "markdown", "root": "/tmp"},
        {"oops": "missing name"},
        "not a dict",
    ]}))
    loaded = sources_mod._load_overlay(path=overlay)
    assert len(loaded) == 1
    assert loaded[0].name == "ok"


def test_apply_overlay_does_not_override_hand_coded(tmp_path, monkeypatch):
    # Force the overlay path to a tmp file containing a conflicting
    # name. Then apply and confirm the hand-coded entry won.
    overlay = tmp_path / "kb-sources.json"
    overlay.write_text(json.dumps({"sources": [
        {
            "name": "foundry-docs",
            "kind": "jsonl",
            "root": "/tmp",
            "description": "should not win",
        },
    ]}))
    monkeypatch.setattr(sources_mod, "_overlay_path", lambda: overlay)
    # Snapshot the hand-coded version then re-apply.
    original = sources_mod.KNOWN_SOURCES["foundry-docs"]
    sources_mod._apply_overlay()
    assert sources_mod.KNOWN_SOURCES["foundry-docs"] is original
