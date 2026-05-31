"""FAST_BACKSTOP ladder + availability-respecting backstop (routing reliability).

The old backstop fell to the first non-auto chain entry (the head = Supreme)
when nothing resolved, bypassing the availability check entirely. That is the
"every prompt silently lands on slow Supreme" bug. The backstop must instead
prefer a multi-provider fast ladder, and when nothing resolves, pick the first
*available* chain entry — never blindly the head.
"""

from config.router import route_prompt, FAST_BACKSTOP

_CHAIN = [
    {
        "name": "crowelm-auto",
        "label": "CroweLM Auto",
        "provider": "auto",
        "aliases": ["auto"],
    },
    {
        "name": "gpt-5.5",
        "label": "CroweLM Supreme",
        "provider": "azure_openai",
        "aliases": ["supreme"],
    },
    {
        "name": "grok-4-1-fast-non-r",
        "label": "CroweLM Swift Raw",
        "provider": "azure_openai",
        "aliases": ["swift"],
    },
    {
        "name": "gpt-5.4-nano",
        "label": "CroweLM Cinder",
        "provider": "azure_openai",
        "aliases": ["cinder"],
    },
]


def test_greeting_never_routes_to_supreme_when_fast_tier_available():
    decision = route_prompt("how are you", chain=_CHAIN, availability=lambda c: True)
    assert decision.primary_label != "CroweLM Supreme"


def test_backstop_picks_available_fast_tier_not_supreme():
    def avail(cfg):
        return cfg.get("name") in {"grok-4-1-fast-non-r", "gpt-5.4-nano"}

    decision = route_prompt(
        "an ambiguous nothing prompt zzz", chain=_CHAIN, availability=avail
    )
    assert decision.primary.get("name") in {"grok-4-1-fast-non-r", "gpt-5.4-nano"}
    assert decision.primary_label != "CroweLM Supreme"


def test_empty_backstop_branch_picks_first_available_not_head():
    # Nothing in preferences or FAST_BACKSTOP resolves in this chain, and the
    # head is NOT available — must fall to the first *available* entry.
    chain = [
        {"name": "crowelm-auto", "label": "CroweLM Auto", "provider": "auto"},
        {"name": "zzz-head", "label": "CroweLM Supreme", "provider": "azure_openai"},
        {"name": "zzz-tail", "label": "CroweLM Tail", "provider": "azure_openai"},
    ]
    decision = route_prompt(
        "zzz", chain=chain, availability=lambda c: c.get("name") == "zzz-tail"
    )
    assert decision.primary.get("name") == "zzz-tail"


def test_fast_backstop_is_multiprovider():
    # A cross-provider (NIM Talon) floor must exist so an Azure-wide outage
    # still has somewhere to land.
    joined = " ".join(s.lower() for s in FAST_BACKSTOP)
    assert "talon" in joined
