# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
"""
Crowe Logic Foundry — doctor.

Comprehensive preflight diagnostics. The deploy() command probes model
endpoints; doctor checks the *environment* that deploy assumes: Python
venv, env files, provider auth, Ollama daemon, agent YAML, models
registry hygiene, and disk pressure.

Each check returns a CheckResult; `run_all_checks()` aggregates them.
Render via `render_table()` (interactive) or `render_json()` (CI). The
`overall_exit_code()` helper maps results to a shell exit code:
  0 = all ok or skipped
  1 = at least one FAIL
  2 = at least one WARN, no FAIL
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Literal

Status = Literal["ok", "warn", "fail", "skip"]


@dataclass
class CheckResult:
    name: str
    category: str
    status: Status
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)


PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_venv() -> CheckResult:
    venv_py = PROJECT_ROOT / ".venv" / "bin" / "python"
    if not venv_py.exists():
        return CheckResult(
            "python venv", "runtime", "fail",
            ".venv/bin/python missing. Run: make install",
        )
    try:
        out = subprocess.run(
            [str(venv_py), "--version"], capture_output=True, text=True, timeout=5
        )
        version = out.stdout.strip() or out.stderr.strip()
    except Exception as exc:
        return CheckResult(
            "python venv", "runtime", "fail",
            f"Could not invoke .venv/bin/python: {exc}",
        )
    # Parse "Python 3.13.12" -> (3, 13)
    parts = version.split()
    if len(parts) >= 2:
        try:
            major, minor, *_ = [int(p) for p in parts[1].split(".")]
        except ValueError:
            major, minor = (0, 0)
        if (major, minor) < (3, 10):
            return CheckResult(
                "python venv", "runtime", "fail",
                f"Python 3.10+ required, found {version}",
                detail={"version": version},
            )
    return CheckResult(
        "python venv", "runtime", "ok",
        version,
        detail={"path": str(venv_py), "version": version},
    )


def check_env_files() -> list[CheckResult]:
    candidates = [
        (PROJECT_ROOT / ".env", "warn"),
        (PROJECT_ROOT / ".env.local", "warn"),
        (Path.home() / ".env.secrets", "fail"),
    ]
    results: list[CheckResult] = []
    for path, missing_severity in candidates:
        if path.exists():
            mode = oct(path.stat().st_mode & 0o777)
            warn_perm = False
            if path == Path.home() / ".env.secrets" and (path.stat().st_mode & 0o077):
                warn_perm = True
            if warn_perm:
                results.append(CheckResult(
                    path.name, "config", "warn",
                    f"World/group readable ({mode}); should be 600",
                    detail={"path": str(path), "mode": mode},
                ))
            else:
                results.append(CheckResult(
                    path.name, "config", "ok",
                    f"present ({mode})",
                    detail={"path": str(path), "mode": mode},
                ))
        else:
            results.append(CheckResult(
                path.name, "config", missing_severity,
                f"missing at {path}",
                detail={"path": str(path)},
            ))
    return results


REQUIRED_ENV_VARS: dict[str, tuple[str, ...]] = {
    "azure-core": ("AZURE_CORE_ENDPOINT", "AZURE_CORE_API_KEY"),
    "anthropic": ("AZURE_ANTHROPIC_ENDPOINT", "AZURE_ANTHROPIC_API_KEY"),
}


def check_required_env_vars() -> list[CheckResult]:
    """Per-provider presence check. Does NOT print values."""
    results: list[CheckResult] = []
    for provider, vars_ in REQUIRED_ENV_VARS.items():
        missing = [v for v in vars_ if not os.environ.get(v)]
        if not missing:
            results.append(CheckResult(
                f"env: {provider}", "secrets", "ok",
                f"all {len(vars_)} vars set",
                detail={"vars": list(vars_)},
            ))
        else:
            results.append(CheckResult(
                f"env: {provider}", "secrets", "warn",
                f"missing: {', '.join(missing)}",
                detail={"missing": missing, "expected": list(vars_)},
            ))
    return results


def check_azure_auth() -> CheckResult:
    if not shutil.which("az"):
        return CheckResult(
            "azure cli", "auth", "warn",
            "az not installed",
        )
    try:
        out = subprocess.run(
            ["az", "account", "show", "--query", "{sub:id,name:name,user:user.name}", "-o", "json"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0:
            return CheckResult(
                "azure cli", "auth", "warn",
                "not logged in (run: az login)",
                detail={"stderr": out.stderr[-200:]},
            )
        data = json.loads(out.stdout)
        return CheckResult(
            "azure cli", "auth", "ok",
            f"logged in as {data.get('user', '?')}",
            detail=data,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            "azure cli", "auth", "warn",
            "az account show timed out (>10s)",
        )
    except Exception as exc:
        return CheckResult(
            "azure cli", "auth", "warn",
            f"az probe failed: {exc}",
        )


def check_ollama_daemon() -> CheckResult:
    """Per memory: a stray `ollama launch claude` can hold the binary
    name without serving 11434. Port probe is the authoritative check.
    """
    try:
        with socket.create_connection(("127.0.0.1", 11434), timeout=2):
            return CheckResult(
                "ollama daemon", "runtime", "ok",
                "serving on 127.0.0.1:11434",
            )
    except (OSError, socket.timeout):
        return CheckResult(
            "ollama daemon", "runtime", "warn",
            "not listening on :11434 (run: ollama serve &)",
        )


def check_ollama_tags() -> list[CheckResult]:
    """Verify each ollama-provider model in agent_config has its tag
    locally mounted. Lightweight: uses `ollama list`.
    """
    try:
        out = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=5
        )
        if out.returncode != 0:
            return [CheckResult(
                "ollama tags", "runtime", "warn",
                "ollama list failed",
            )]
        mounted: set[str] = set()
        for line in out.stdout.splitlines()[1:]:  # skip header
            parts = line.split()
            if parts:
                mounted.add(parts[0])
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return [CheckResult(
            "ollama tags", "runtime", "skip",
            "ollama CLI unavailable",
        )]

    # Discover expected tags from agent_config without importing it
    # (importing the full module is heavy). We grep for ollama-provider
    # entries instead.
    try:
        agent_config = (PROJECT_ROOT / "config" / "agent_config.py").read_text(encoding="utf-8")
    except OSError:
        return [CheckResult(
            "ollama tags", "config", "skip",
            "agent_config.py unreadable",
        )]

    import re
    # Match: provider='ollama' followed (within ~200 chars) by name='tag:variant'
    expected: set[str] = set()
    for match in re.finditer(r"provider\s*=\s*['\"]ollama['\"][^}]{0,400}", agent_config):
        block = match.group(0)
        name_match = re.search(r"name\s*=\s*['\"]([^'\"]+)['\"]", block)
        if name_match:
            expected.add(name_match.group(1))

    results: list[CheckResult] = []
    for tag in sorted(expected):
        if tag in mounted:
            results.append(CheckResult(
                f"ollama: {tag}", "runtime", "ok",
                "mounted",
            ))
        else:
            results.append(CheckResult(
                f"ollama: {tag}", "runtime", "warn",
                f"declared but not mounted (run: ollama pull {tag})",
            ))
    if not expected:
        results.append(CheckResult(
            "ollama tags", "runtime", "skip",
            "no ollama-provider entries found in agent_config.py",
        ))
    return results


def check_agent_yaml() -> list[CheckResult]:
    agents_dir = PROJECT_ROOT / "agents"
    if not agents_dir.is_dir():
        return [CheckResult(
            "agents dir", "agents", "fail",
            f"missing: {agents_dir}",
        )]
    try:
        import yaml  # noqa
    except ImportError:
        return [CheckResult(
            "agent yaml", "agents", "skip",
            "PyYAML not importable in current interpreter",
        )]
    results: list[CheckResult] = []
    yaml_files = sorted(agents_dir.glob("*.yaml"))
    if not yaml_files:
        return [CheckResult(
            "agents dir", "agents", "warn",
            "no *.yaml files in agents/",
        )]
    for yf in yaml_files:
        try:
            data = yaml.safe_load(yf.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                results.append(CheckResult(
                    f"agent: {yf.stem}", "agents", "fail",
                    "top-level YAML is not a mapping",
                ))
                continue
            missing = [k for k in ("name", "description") if k not in data]
            if missing:
                results.append(CheckResult(
                    f"agent: {yf.stem}", "agents", "warn",
                    f"missing keys: {', '.join(missing)}",
                ))
            else:
                results.append(CheckResult(
                    f"agent: {yf.stem}", "agents", "ok",
                    f"valid ({len(data)} keys)",
                ))
        except Exception as exc:
            results.append(CheckResult(
                f"agent: {yf.stem}", "agents", "fail",
                f"parse error: {exc}",
            ))
    return results


_INFRASTRUCTURE_TOOL_FILES: frozenset[str] = frozenset({
    "registry",          # auto-discovery infra
    "control_center",    # FastAPI app
    "mobile_signaling",  # FastAPI app
    "audit_log",         # logging helper
    "mcp_client",        # MCP transport client
    "staging_pipeline",  # internal pipeline state
})


def _module_is_infrastructure(tree: Any) -> bool:
    """Heuristic: a tools/ module that imports FastAPI/Starlette is a
    service, not a callable tool surface — skip it.
    """
    import ast
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mod = node.module.split(".")[0]
            if mod in ("fastapi", "starlette"):
                return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in ("fastapi", "starlette"):
                    return True
    return False


def check_tool_docstrings() -> list[CheckResult]:
    """Lint each tools/*.py public function for Azure schema compatibility.

    Tools auto-discover via `tools/registry.py:auto_discover_tools` — no
    `@tool` decorator. The convention is: top-level public function in
    a tools/ module IS a tool, unless the module is infrastructure
    (FastAPI app, registry plumbing, etc).

    Per public function, requires:
      - a docstring
      - `:param <arg>:` for every positional arg (excluding self/cls)
      - `:return:` (or `:returns:`)
      - `:rtype:`

    Files explicitly in `_INFRASTRUCTURE_TOOL_FILES` and modules that
    import FastAPI/Starlette are skipped.
    """
    import ast
    tools_dir = PROJECT_ROOT / "tools"
    if not tools_dir.is_dir():
        return [CheckResult(
            "tools dir", "tools", "skip",
            f"missing: {tools_dir}",
        )]
    results: list[CheckResult] = []
    for path in sorted(tools_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        if path.stem in _INFRASTRUCTURE_TOOL_FILES:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            results.append(CheckResult(
                f"tool: {path.stem}", "tools", "fail",
                f"syntax error at line {exc.lineno}: {exc.msg}",
            ))
            continue
        if _module_is_infrastructure(tree):
            continue
        issues: list[dict[str, Any]] = []
        public_fns = 0
        # Only scan top-level functions (depth 0). Nested helpers don't
        # become tools.
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("_"):
                continue
            public_fns += 1
            args = [
                a.arg for a in node.args.args
                if a.arg not in ("self", "cls")
            ]
            doc = ast.get_docstring(node) or ""
            fn_issues: list[str] = []
            if not doc.strip():
                fn_issues.append("missing docstring")
            else:
                for a in args:
                    if f":param {a}" not in doc:
                        fn_issues.append(f"missing :param {a}:")
                if ":return:" not in doc and ":returns:" not in doc:
                    fn_issues.append("missing :return:")
                if ":rtype:" not in doc:
                    fn_issues.append("missing :rtype:")
            if fn_issues:
                issues.append({
                    "function": node.name,
                    "line": node.lineno,
                    "issues": fn_issues,
                })
        if public_fns == 0:
            continue
        if issues:
            sample = "; ".join(
                f"{i['function']}:{i['issues'][0]}" for i in issues[:3]
            )
            more = f" (+{len(issues) - 3} more)" if len(issues) > 3 else ""
            results.append(CheckResult(
                f"tool: {path.stem}", "tools", "warn",
                f"{len(issues)}/{public_fns} fn(s) with gaps: {sample}{more}",
                detail={"file": str(path), "public_fns": public_fns, "issues": issues},
            ))
        else:
            results.append(CheckResult(
                f"tool: {path.stem}", "tools", "ok",
                f"{public_fns} fn(s), all docstrings valid",
            ))
    return results


def check_models_registry() -> list[CheckResult]:
    """Validate JSON, count entries, and scan for leaky labels via the
    rebrand map's authoritative leak detector.
    """
    results: list[CheckResult] = []
    candidates = [
        PROJECT_ROOT / "config" / "models.extra.json",
        Path.home() / ".config" / "crowe-logic" / "models.extra.json",
    ]
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from config.crowelm.rebrand_map import unmapped_leaky_names
    except Exception as exc:
        return [CheckResult(
            "rebrand_map", "registry", "fail",
            f"import failed: {exc}",
        )]
    for path in candidates:
        # Disambiguate by parent (both files share the basename
        # "models.extra.json" but live in different directories).
        label = f"registry: {path.parent.name}/{path.name}"
        if not path.exists():
            results.append(CheckResult(
                label, "registry", "skip",
                f"not present at {path}",
            ))
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            entries = data.get("models", data) if isinstance(data, dict) else data
            if not isinstance(entries, list):
                results.append(CheckResult(
                    label, "registry", "fail",
                    "models payload is not a list",
                ))
                continue
            leaks = unmapped_leaky_names(entries)
            if leaks:
                sample = ", ".join(f"{n}→{l}" for n, l in leaks[:3])
                more = f" (+{len(leaks) - 3} more)" if len(leaks) > 3 else ""
                results.append(CheckResult(
                    label, "registry", "fail",
                    f"{len(leaks)} leaky labels: {sample}{more}",
                    detail={"leaks": leaks, "path": str(path)},
                ))
            else:
                results.append(CheckResult(
                    label, "registry", "ok",
                    f"{len(entries)} entries, no leaks",
                    detail={"count": len(entries), "path": str(path)},
                ))
        except Exception as exc:
            results.append(CheckResult(
                label, "registry", "fail",
                f"parse error: {exc}",
            ))
    return results


def check_disk_space() -> CheckResult:
    """Memory rule: / runs ~95% full → ENOSPC blocks tool calls.
    Surface this early.
    """
    usage = shutil.disk_usage("/")
    pct_used = (usage.used / usage.total) * 100
    free_gb = usage.free / (1024**3)
    if pct_used >= 95:
        status: Status = "fail"
    elif pct_used >= 90:
        status = "warn"
    else:
        status = "ok"
    return CheckResult(
        "disk: /", "runtime", status,
        f"{pct_used:.1f}% used, {free_gb:.1f} GB free",
        detail={"pct_used": round(pct_used, 1), "free_gb": round(free_gb, 1)},
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_all_checks(*, skip_slow: bool = False, skip_tools: bool = False) -> list[CheckResult]:
    results: list[CheckResult] = []
    results.append(check_venv())
    results.extend(check_env_files())
    results.extend(check_required_env_vars())
    results.append(check_disk_space())
    if not skip_slow:
        results.append(check_azure_auth())
        results.append(check_ollama_daemon())
        results.extend(check_ollama_tags())
    results.extend(check_agent_yaml())
    results.extend(check_models_registry())
    if not skip_tools:
        results.extend(check_tool_docstrings())
    return results


def overall_exit_code(results: list[CheckResult]) -> int:
    statuses = {r.status for r in results}
    if "fail" in statuses:
        return 1
    if "warn" in statuses:
        return 2
    return 0


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_STATUS_GLYPH: dict[Status, tuple[str, str]] = {
    "ok": ("OK", "green"),
    "warn": ("WARN", "yellow"),
    "fail": ("FAIL", "red"),
    "skip": ("SKIP", "dim"),
}


def render_table(results: list[CheckResult], console: Any = None) -> None:
    """Pretty-print to a rich console. Imports rich lazily."""
    from rich.console import Console
    from rich.table import Table

    console = console or Console()
    by_category: dict[str, list[CheckResult]] = {}
    for r in results:
        by_category.setdefault(r.category, []).append(r)

    for category in sorted(by_category):
        table = Table(
            title=f"[bold]{category}[/]",
            show_header=True,
            header_style="bold",
            title_justify="left",
            padding=(0, 1),
        )
        table.add_column("status", width=6, no_wrap=True)
        table.add_column("check")
        table.add_column("detail", overflow="fold")
        for r in by_category[category]:
            glyph, color = _STATUS_GLYPH[r.status]
            table.add_row(f"[{color}]{glyph}[/]", r.name, r.reason)
        console.print(table)
        console.print()

    counts = {s: 0 for s in _STATUS_GLYPH}
    for r in results:
        counts[r.status] += 1
    summary = " ".join(
        f"[{_STATUS_GLYPH[s][1]}]{counts[s]} {_STATUS_GLYPH[s][0].lower()}[/]"
        for s in ("ok", "warn", "fail", "skip")
        if counts[s]
    )
    console.print(f"[bold]summary:[/] {summary}")


def render_json(results: list[CheckResult]) -> str:
    return json.dumps(
        {
            "results": [asdict(r) for r in results],
            "exit_code": overall_exit_code(results),
        },
        indent=2,
    )


# ---------------------------------------------------------------------------
# Self-repair
# ---------------------------------------------------------------------------

def fix_leaky_labels(results: list[CheckResult]) -> list[dict[str, Any]]:
    """Apply REBRAND_MAP to any leaky entries surfaced by check_models_registry.

    For each leaky (name, label) pair where REBRAND_MAP has a codename:
      - replace `label` with the codename
      - push the old leaky label into `aliases` (deduped)
      - back up the file as <path>.bak.<timestamp> on first write

    Returns a list of per-file repair reports.
    """
    import time
    from config.crowelm.rebrand_map import REBRAND_MAP

    reports: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for r in results:
        if r.status != "fail":
            continue
        path_str = r.detail.get("path")
        if not path_str or path_str in seen_paths:
            continue
        leaks = r.detail.get("leaks") or []
        if not leaks:
            continue
        seen_paths.add(path_str)
        path = Path(path_str)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            reports.append({"path": path_str, "ok": False, "error": str(exc)})
            continue
        entries = payload.get("models") if isinstance(payload, dict) else payload
        if not isinstance(entries, list):
            reports.append({"path": path_str, "ok": False, "error": "not a list"})
            continue

        leak_names = {n for n, _ in leaks}
        renamed: list[tuple[str, str, str]] = []
        skipped: list[str] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", "")).strip()
            if name not in leak_names:
                continue
            new_label = REBRAND_MAP.get(name)
            if not new_label:
                # case-insensitive fallback
                for k, v in REBRAND_MAP.items():
                    if k.lower() == name.lower():
                        new_label = v
                        break
            if not new_label:
                skipped.append(name)
                continue
            old_label = str(entry.get("label", "")).strip()
            entry["label"] = new_label
            aliases = entry.get("aliases") or []
            if not isinstance(aliases, list):
                aliases = []
            if old_label and old_label not in aliases:
                aliases.append(old_label)
            entry["aliases"] = aliases
            renamed.append((name, old_label, new_label))

        if renamed:
            ts = time.strftime("%Y%m%d-%H%M%S")
            backup = path.with_suffix(path.suffix + f".bak.{ts}")
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        reports.append({
            "path": path_str,
            "ok": True,
            "renamed": [
                {"name": n, "old": o, "new": w} for n, o, w in renamed
            ],
            "skipped_no_map_entry": skipped,
            "backup": str(backup) if renamed else None,
        })
    return reports


__all__ = [
    "CheckResult",
    "run_all_checks",
    "overall_exit_code",
    "render_table",
    "render_json",
    "fix_leaky_labels",
]
