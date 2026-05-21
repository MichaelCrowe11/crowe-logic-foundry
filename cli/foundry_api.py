"""Client for the Crowe Logic Foundry control plane.

Minimal HTTP client the CLI uses to authenticate against the control
plane, check a workspace's credit balance, and consume credits as
turns are dispatched. Designed to fail open: if the control plane is
unreachable or ``CROWE_LOGIC_API_KEY`` is unset, the CLI keeps running
in BYOK mode with the credit meter disabled.

Configuration
-------------

Environment variables, read at import time:

    CROWE_LOGIC_API_KEY      API key issued by the control plane.
                             Launch format:
                             `crowe_pat_<workspace_id>_<secret>`.
                             Legacy `clk_<workspace_id>_<secret>`
                             keys are still accepted.
                             When unset, the client operates in
                             ``disabled`` mode and every method
                             returns a tombstone that the CLI
                             treats as "no enforcement".

    CROWE_LOGIC_API_URL      Base URL of the control plane. Default:
                             ``https://api.crowelogic.com``.

    CROWE_LOGIC_BYOK         When set to "1" or "true", forces BYOK
                             mode regardless of whether an API key
                             is present. Useful for developers who
                             have a Pro account but want to test
                             with their own provider keys without
                             consuming credits.

Fail-open behavior
------------------

If a HTTP request times out or returns 5xx, the client returns a
``CreditDecision.allow_fallback`` sentinel and the CLI continues
with the turn. Tracking these and charging retroactively is a
future pass. What we do NOT want is a dead control plane killing
every CLI invocation mid-thought.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Optional

def _workspace_id_from_api_key(raw_key: str) -> Optional[str]:
    if raw_key.startswith("clk_"):
        parts = raw_key.split("_", 2)
        return parts[1] if len(parts) >= 3 and parts[1] else None
    if raw_key.startswith("crowe_pat_"):
        body = raw_key[len("crowe_pat_"):]
        parts = body.split("_", 1)
        return parts[0] if len(parts) == 2 and parts[0] else None
    return None

try:
    import httpx
except ImportError:
    httpx = None   # fail-open will trigger in every code path below


# ---- Config loaded lazily -------------------------------------------------

_lock = threading.Lock()
_client_singleton: Optional["FoundryAPIClient"] = None


def _truthy(value: Optional[str]) -> bool:
    return bool(value) and value.strip().lower() in ("1", "true", "yes", "on")


def get_client() -> "FoundryAPIClient":
    """Return the process-wide client, constructing it lazily."""
    global _client_singleton
    with _lock:
        if _client_singleton is None:
            _client_singleton = FoundryAPIClient()
        return _client_singleton


def reset_client() -> None:
    """Reset the cached singleton. Tests use this to re-read env."""
    global _client_singleton
    with _lock:
        _client_singleton = None


# ---- Data classes --------------------------------------------------------

@dataclass(frozen=True)
class CreditDecision:
    """Result of a pre-turn credit check."""
    allowed: bool
    balance: int
    tier: str
    reason: str
    via_fallback: bool = False

    @classmethod
    def allow_fallback(cls, reason: str) -> "CreditDecision":
        """Fail-open decision used when the control plane can't be reached."""
        return cls(
            allowed=True, balance=-1, tier="unknown",
            reason=reason, via_fallback=True,
        )

    @classmethod
    def allow_byok(cls) -> "CreditDecision":
        return cls(
            allowed=True, balance=-1, tier="byok",
            reason="BYOK mode: credit enforcement disabled",
        )

    @classmethod
    def deny(cls, balance: int, tier: str, reason: str) -> "CreditDecision":
        return cls(allowed=False, balance=balance, tier=tier, reason=reason)


@dataclass(frozen=True)
class AccountStatus:
    """Snapshot of the workspace's billing state for the /account command."""
    authenticated: bool
    byok: bool
    tier: str
    balance: int
    allocation: int
    reset_at: Optional[str]
    active: bool
    workspace_id: Optional[str]
    message: str


# ---- Client --------------------------------------------------------------

class FoundryAPIClient:
    """HTTP client around the control plane credit endpoints.

    Construct via :func:`get_client` so tests and the CLI share one
    instance. Direct instantiation is fine for unit tests that inject
    a mock httpx.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        byok_mode: Optional[bool] = None,
        http_timeout: float = 6.0,
    ):
        self.api_key = api_key if api_key is not None else os.environ.get("CROWE_LOGIC_API_KEY", "").strip()
        self.base_url = (base_url if base_url is not None else os.environ.get(
            "CROWE_LOGIC_API_URL", "https://api.crowelogic.com",
        )).rstrip("/")
        self.byok_mode = byok_mode if byok_mode is not None else _truthy(os.environ.get("CROWE_LOGIC_BYOK", ""))
        self.http_timeout = http_timeout

        self._workspace_id: Optional[str] = None
        if self.api_key:
            self._workspace_id = _workspace_id_from_api_key(self.api_key)

    # ── Mode helpers ─────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        """True when the client will make real HTTP calls."""
        return (
            bool(self.api_key)
            and not self.byok_mode
            and httpx is not None
            and self._workspace_id is not None
        )

    @property
    def workspace_id(self) -> Optional[str]:
        return self._workspace_id

    # ── Core operations ──────────────────────────────────────────

    def check_and_reserve(self, credits: int, *, model_label: str = "") -> CreditDecision:
        """Pre-turn decision. Consumes credits up-front, fails open on error.

        The CLI should call this BEFORE streaming a turn. On a 402
        response the caller must abort the turn and surface the
        reason to the user. On any transport or 5xx, the caller
        continues (fail-open) so the control plane being down never
        blocks a paying user mid-thought.
        """
        if self.byok_mode:
            return CreditDecision.allow_byok()
        if not self.enabled:
            return CreditDecision.allow_fallback("No API key set; BYOK mode implicit")

        url = f"{self.base_url}/api/workspaces/{self._workspace_id}/credits/consume"
        payload = {
            "amount": max(credits, 1),
            "reason": "turn",
            "model_label": model_label or None,
            "metadata": {},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=self.http_timeout) as client:
                response = client.post(url, headers=headers, json=payload)
        except Exception as exc:
            return CreditDecision.allow_fallback(f"{type(exc).__name__}: {exc}")

        if response.status_code == 200:
            body = response.json()
            return CreditDecision(
                allowed=True,
                balance=body.get("balance", 0),
                tier="",  # consume does not return tier; /account shows it
                reason="ok",
            )
        if response.status_code == 402:
            try:
                detail = response.json().get("detail", "Insufficient credits")
            except Exception:
                detail = "Insufficient credits"
            # A 402 always denies. Balance parsing is best-effort.
            balance = 0
            tier = "unknown"
            return CreditDecision.deny(balance=balance, tier=tier, reason=detail)
        if 500 <= response.status_code < 600:
            return CreditDecision.allow_fallback(
                f"Control plane 5xx ({response.status_code})"
            )
        # 4xx other than 402 means config/auth issue. Fail open for now,
        # but surface the reason so the operator knows something's off.
        return CreditDecision.allow_fallback(
            f"Control plane returned {response.status_code}: {response.text[:200]}"
        )

    def account_status(self) -> AccountStatus:
        """Fetch the workspace's current balance, tier, and reset date."""
        if self.byok_mode:
            return AccountStatus(
                authenticated=bool(self.api_key),
                byok=True,
                tier="byok",
                balance=-1, allocation=0, reset_at=None,
                active=True,
                workspace_id=self._workspace_id,
                message="BYOK mode: you pay providers directly, no credit meter",
            )
        if not self.enabled:
            return AccountStatus(
                authenticated=False,
                byok=False,
                tier="unknown",
                balance=-1, allocation=0, reset_at=None,
                active=False,
                workspace_id=None,
                message="No API key set. Run `crowe-logic login` or set CROWE_LOGIC_API_KEY.",
            )

        url = f"{self.base_url}/api/workspaces/{self._workspace_id}/credits"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            with httpx.Client(timeout=self.http_timeout) as client:
                response = client.get(url, headers=headers)
        except Exception as exc:
            return AccountStatus(
                authenticated=True, byok=False, tier="unknown",
                balance=-1, allocation=0, reset_at=None, active=False,
                workspace_id=self._workspace_id,
                message=f"Control plane unreachable: {type(exc).__name__}: {exc}",
            )

        if response.status_code == 200:
            body = response.json()
            return AccountStatus(
                authenticated=True,
                byok=False,
                tier=body.get("tier_key", "unknown"),
                balance=body.get("balance", 0),
                allocation=body.get("allocation", 0),
                reset_at=body.get("reset_at"),
                active=body.get("active", True),
                workspace_id=self._workspace_id,
                message="ok",
            )
        return AccountStatus(
            authenticated=True, byok=False, tier="unknown",
            balance=-1, allocation=0, reset_at=None, active=False,
            workspace_id=self._workspace_id,
            message=f"Control plane returned {response.status_code}",
        )

    def correct(self, actual_credits: int, estimated_credits: int, *, model_label: str = "") -> None:
        """Optional post-turn correction when estimate != actual.

        Fire-and-forget. If the correction request fails, we don't
        disrupt the turn; the transaction history just has a small
        drift we can reconcile later.
        """
        delta = actual_credits - estimated_credits
        if delta == 0 or not self.enabled:
            return

        url = f"{self.base_url}/api/workspaces/{self._workspace_id}/credits/consume"
        amount = abs(delta)
        # For a refund we'd use a negative-amount API; the current
        # consume endpoint only accepts positive amounts. If delta is
        # negative (we over-reserved), skip the correction for now.
        if delta < 0:
            return

        payload = {
            "amount": amount,
            "reason": "correction",
            "model_label": model_label or None,
            "metadata": {"estimated": estimated_credits, "actual": actual_credits},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=self.http_timeout) as client:
                client.post(url, headers=headers, json=payload)
        except Exception:
            pass
