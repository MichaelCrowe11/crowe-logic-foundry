# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
"""
Cross-surface discovery for the foundry.

Ranked keyword + fuzzy search across:
  - Models: REBRAND_MAP entries and models.extra.json registry
  - Agents: agents/*.yaml (name, description, tools, model)
  - Tools:  tools/*.py public functions + their docstrings

No embeddings — pure scoring. Optimized for "what tool talks to Cohere?"
style queries where the answer is one keyword away.

Scoring (per item):
  +10  query is a substring of the item's `name`
  +5   query is a substring of the item's `label` / `display`
  +3   query is a substring of the item's `description` / docstring
  +1   any query token appears anywhere in the searchable text
  +2   bonus if every query token appears (AND match)
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Literal

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ItemKind = Literal["model", "agent", "tool"]
_INFRASTRUCTURE_TOOL_FILES: frozenset[str] = frozenset({
    "registry",
    "control_center",
    "mobile_signaling",
    "audit_log",
    "mcp_client",
    "staging_pipeline",
})


@dataclass
class Item:
    kind: ItemKind
    name: str
    display: str
    description: str = ""
    source: str = ""
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class Hit:
    item: Item
    score: int
    matched_terms: list[str]


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------

def _index_models() -> list[Item]:
    items: list[Item] = []
    try:
        from config.crowelm.rebrand_map import REBRAND_MAP
        for name, label in REBRAND_MAP.items():
            items.append(Item(
                kind="model",
                name=name,
                display=label,
                description=f"Crowe Logic codename for Azure deployment {name!r}.",
                source="config/crowelm/rebrand_map.py",
            ))
    except Exception:
        pass

    # Project-local registry (post-rebrand SOT)
    for path in [
        PROJECT_ROOT / "config" / "models.extra.json",
        Path.home() / ".config" / "crowe-logic" / "models.extra.json",
    ]:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        entries = data.get("models") if isinstance(data, dict) else data
        if not isinstance(entries, list):
            continue
        for e in entries:
            if not isinstance(e, dict):
                continue
            name = str(e.get("name", "")).strip()
            label = str(e.get("label", name)).strip()
            if not name:
                continue
            items.append(Item(
                kind="model",
                name=name,
                display=label,
                description=(
                    f"provider={e.get('provider', '?')} type={e.get('type', '?')} "
                    f"surface={e.get('surface', '-')}"
                ),
                source=str(path.relative_to(path.parent.parent) if path.parent.parent.exists() else path),
                extras={"aliases": e.get("aliases", [])},
            ))
    return items


def _index_agents() -> list[Item]:
    items: list[Item] = []
    agents_dir = PROJECT_ROOT / "agents"
    if not agents_dir.is_dir():
        return items
    try:
        import yaml
    except ImportError:
        return items
    for yf in sorted(agents_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(yf.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        name = str(data.get("name", yf.stem)).strip()
        description = str(data.get("description", "")).strip()
        tools = data.get("tools") or []
        model = data.get("model", "?")
        items.append(Item(
            kind="agent",
            name=name,
            display=name,
            description=f"model={model} tools={len(tools) if isinstance(tools, list) else '?'} | {description}",
            source=str(yf.relative_to(PROJECT_ROOT)),
            extras={"tools": tools, "model": model},
        ))
    return items


def _index_tools() -> list[Item]:
    items: list[Item] = []
    tools_dir = PROJECT_ROOT / "tools"
    if not tools_dir.is_dir():
        return items
    for path in sorted(tools_dir.glob("*.py")):
        if path.name == "__init__.py" or path.stem in _INFRASTRUCTURE_TOOL_FILES:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        # Skip FastAPI/Starlette modules — same heuristic as doctor.
        is_infra = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in ("fastapi", "starlette"):
                    is_infra = True
                    break
            elif isinstance(node, ast.Import):
                if any(a.name.split(".")[0] in ("fastapi", "starlette") for a in node.names):
                    is_infra = True
                    break
        if is_infra:
            continue
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("_"):
                continue
            doc = (ast.get_docstring(node) or "").strip()
            # First non-empty sentence-ish chunk for display
            short = re.split(r"[.!?]\s|\n\s*\n", doc, maxsplit=1)[0].strip()[:200]
            items.append(Item(
                kind="tool",
                name=node.name,
                display=node.name,
                description=short or "(no docstring)",
                source=str(path.relative_to(PROJECT_ROOT)),
                extras={"module": path.stem, "lineno": node.lineno},
            ))
    return items


def build_index() -> list[Item]:
    """Build the full discovery index. Cheap enough to run per call."""
    return _index_models() + _index_agents() + _index_tools()


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _score(item: Item, query: str, query_tokens: set[str]) -> tuple[int, list[str]]:
    name_l = item.name.lower()
    display_l = item.display.lower()
    desc_l = item.description.lower()
    blob = " ".join([name_l, display_l, desc_l, item.source.lower()])
    q = query.lower().strip()

    score = 0
    matched: list[str] = []

    if q and q in name_l:
        score += 10
        matched.append(f"name~{q!r}")
    if q and q in display_l:
        score += 5
        matched.append(f"display~{q!r}")
    if q and q in desc_l:
        score += 3
        matched.append(f"desc~{q!r}")

    hits_per_token = 0
    for t in query_tokens:
        if t in blob:
            hits_per_token += 1
    if hits_per_token:
        score += hits_per_token
        if hits_per_token == len(query_tokens) and len(query_tokens) > 1:
            score += 2  # AND-match bonus
    return score, matched


def search(
    query: str,
    *,
    kind: ItemKind | None = None,
    limit: int = 15,
) -> list[Hit]:
    if not query.strip():
        return []
    index = build_index()
    if kind:
        index = [i for i in index if i.kind == kind]
    qt = _tokenize(query)
    hits: list[Hit] = []
    for item in index:
        s, matched = _score(item, query, qt)
        if s > 0:
            hits.append(Hit(item=item, score=s, matched_terms=matched))
    hits.sort(key=lambda h: (-h.score, h.item.kind, h.item.name))
    return hits[:limit]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_KIND_COLOR: dict[ItemKind, str] = {
    "model": "cyan",
    "agent": "green",
    "tool": "magenta",
}


def render_table(hits: list[Hit], query: str, console: Any = None) -> None:
    from rich.console import Console
    from rich.table import Table

    console = console or Console()
    table = Table(
        title=f'[bold]discover[/] [dim]"{query}"[/]  ({len(hits)} hit{"s" if len(hits) != 1 else ""})',
        show_header=True,
        header_style="bold",
        title_justify="left",
        padding=(0, 1),
    )
    table.add_column("score", width=5, justify="right")
    table.add_column("kind", width=6)
    table.add_column("name")
    table.add_column("display")
    table.add_column("source", overflow="fold")
    table.add_column("detail", overflow="fold")

    for h in hits:
        color = _KIND_COLOR.get(h.item.kind, "white")
        table.add_row(
            str(h.score),
            f"[{color}]{h.item.kind}[/]",
            h.item.name,
            h.item.display if h.item.display != h.item.name else "",
            h.item.source,
            h.item.description,
        )
    if not hits:
        console.print(f'[dim]no matches for {query!r}[/]')
        return
    console.print(table)


def render_json(hits: list[Hit]) -> str:
    return json.dumps(
        {
            "hits": [
                {
                    "score": h.score,
                    "matched": h.matched_terms,
                    "item": asdict(h.item),
                }
                for h in hits
            ],
        },
        indent=2,
    )


__all__ = [
    "Item",
    "Hit",
    "build_index",
    "search",
    "render_table",
    "render_json",
]
