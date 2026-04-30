"""
PathPolicy: refuse Write-tool calls that would create files in unsafe locations.

The 2026-04-30 Eclipse session wrote `campaign_blast.py` and `contacts.json`
directly to `/Users/crowelogic/`, violating the home-dir safety rule in
/Users/crowelogic/CLAUDE.md ("Do not write files at ~/ root unless the user
gives that exact path").

This guardrail is the deterministic backstop. It does not replace the system-
prompt rule; it enforces it at the tool boundary so a model that ignored the
prompt cannot still pollute the home directory.

Decisions:
    ALLOW           - path is fine, proceed.
    REQUIRE_CONFIRM - path is risky; ask the user before proceeding.
    DENY            - path is forbidden by policy.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_HOME = Path.home()


@dataclass(frozen=True)
class PathDecision:
    verdict: str  # "ALLOW", "REQUIRE_CONFIRM", "DENY"
    reason: str
    path: str


class PathPolicy:
    """Apply path policy to a candidate Write target.

    Default rules (configurable):
      - DENY writes to filesystem root paths shorter than 4 segments unless
        they sit under an explicitly allowed prefix.
      - REQUIRE_CONFIRM for any file whose parent is exactly $HOME and whose
        path was not provided verbatim by the user this turn.
      - ALLOW everything under $HOME/Projects, $HOME/Documents, /tmp, $TMPDIR,
        and the project working directory.
    """

    DEFAULT_ALLOWED_PREFIXES: tuple[str, ...] = (
        str(_HOME / "Projects"),
        str(_HOME / "Documents"),
        str(_HOME / ".config"),
        str(_HOME / ".local"),
        str(_HOME / ".cache"),
        "/tmp",
        os.environ.get("TMPDIR", "/tmp"),
    )

    # Hard-deny system paths regardless of segment count. macOS resolves
    # /etc to /private/etc which has four segments, so the older "shallow
    # path" heuristic missed it.
    DENY_PREFIXES: tuple[str, ...] = (
        "/etc",
        "/private/etc",
        "/usr",
        "/bin",
        "/sbin",
        "/System",
        "/Library/LaunchAgents",
        "/Library/LaunchDaemons",
        "/var/db",
        "/private/var/db",
    )

    def __init__(
        self,
        home: Path = _HOME,
        allowed_prefixes: tuple[str, ...] | None = None,
        user_provided_paths: frozenset[str] = frozenset(),
        project_root: Path | None = None,
        deny_prefixes: tuple[str, ...] | None = None,
    ):
        # Resolve home once so comparisons survive symlink quirks
        # (macOS /var -> /private/var, /tmp -> /private/tmp, etc.)
        self.home = Path(home).resolve()
        self.allowed_prefixes = allowed_prefixes or self.DEFAULT_ALLOWED_PREFIXES
        self.user_provided_paths = user_provided_paths
        self.project_root = project_root.resolve() if project_root else None
        self.deny_prefixes = deny_prefixes or self.DENY_PREFIXES

    def evaluate(self, candidate_path: str) -> PathDecision:
        try:
            resolved = Path(candidate_path).expanduser().resolve()
        except (OSError, RuntimeError) as exc:
            return PathDecision(
                verdict="DENY",
                reason=f"path resolution failed: {exc}",
                path=candidate_path,
            )

        # User explicitly named this exact path: always allow.
        # Compare both the literal input and the resolved form, since the
        # user might have typed an unresolved path.
        if (
            str(resolved) in self.user_provided_paths
            or candidate_path in self.user_provided_paths
        ):
            return PathDecision(
                verdict="ALLOW", reason="user-provided path", path=str(resolved)
            )

        # Hard-deny system paths first.
        for deny in self.deny_prefixes:
            try:
                resolved.relative_to(Path(deny).resolve())
                return PathDecision(
                    verdict="DENY",
                    reason=f"path is under protected system prefix {deny}",
                    path=str(resolved),
                )
            except ValueError:
                continue

        # Home-dir root files: deny by policy. The 2026-04-30 incident is
        # exactly this case (campaign_blast.py at /Users/crowelogic/).
        # Checked BEFORE allowed-prefix matching because $HOME often contains
        # subdirs like ~/.config that should still be writable, but the home
        # root itself should not.
        if resolved.parent == self.home:
            return PathDecision(
                verdict="DENY",
                reason=(
                    "writes directly to home-dir root violate "
                    "/Users/crowelogic/CLAUDE.md home-dir safety rule"
                ),
                path=str(resolved),
            )

        # Project root subtree: allow.
        if self.project_root is not None:
            try:
                resolved.relative_to(self.project_root)
                return PathDecision(
                    verdict="ALLOW",
                    reason="under project root",
                    path=str(resolved),
                )
            except ValueError:
                pass

        # Allowed prefixes: allow.
        for prefix in self.allowed_prefixes:
            if not prefix:
                continue
            try:
                resolved.relative_to(Path(prefix).resolve())
                return PathDecision(
                    verdict="ALLOW",
                    reason=f"under allowed prefix {prefix}",
                    path=str(resolved),
                )
            except ValueError:
                continue

        # Filesystem root with too few path segments: deny as a last resort.
        parts = resolved.parts
        if len(parts) <= 2:
            return PathDecision(
                verdict="DENY",
                reason=f"path too shallow ({len(parts)} segments)",
                path=str(resolved),
            )

        # Anything else: confirm with user.
        return PathDecision(
            verdict="REQUIRE_CONFIRM",
            reason="path outside allowed prefixes; confirm with user",
            path=str(resolved),
        )
