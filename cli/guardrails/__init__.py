"""
CroweLM guardrail chain.

Deterministic safety filters that run at the streaming boundary, between the
provider response and the renderer. Each guardrail is independently testable.

Public surface:
    SecretScrubber  - redacts API keys and credentials
    StyleEnforcer   - normalizes em-dashes, warns on emoji
    PathPolicy      - refuses Write-tool paths that violate home-dir safety
    ScopeBudget     - interrupts when reasoning-to-output ratio exceeds budget
    GuardrailChain  - composes the above into a single filter pipeline
    GuardrailEvent  - structured signal emitted when a guardrail fires
"""
from cli.guardrails.chain import GuardrailChain, GuardrailEvent
from cli.guardrails.paths import PathPolicy, PathDecision
from cli.guardrails.scope import ScopeBudget, BudgetDecision
from cli.guardrails.secrets import SecretScrubber, SecretHit
from cli.guardrails.style import StyleEnforcer, StyleIssue

__all__ = [
    "GuardrailChain",
    "GuardrailEvent",
    "PathPolicy",
    "PathDecision",
    "ScopeBudget",
    "BudgetDecision",
    "SecretScrubber",
    "SecretHit",
    "StyleEnforcer",
    "StyleIssue",
]
