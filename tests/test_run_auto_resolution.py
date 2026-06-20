"""The single-prompt `run()` path must never dispatch the `auto` meta-model.

With auto-routing disabled (CROWE_LOGIC_AUTO_ROUTE=0), `_current_model()`
returns the `auto` meta-model (provider "auto"). The provider dispatch has no
case for "auto", so it falls through to the legacy Azure-Agents branch and
dies. Resolving `auto` to the first concrete tier in the chain sends the turn
to a real provider (the primary azure_openai tier, which streams reasoning)
and also keeps the signed-in gateway from receiving an "auto" model it rejects.
"""

import cli.crowe_logic as cl
from config.agent_config import MODEL_CHAIN


def test_resolve_auto_picks_first_concrete_chain_tier():
    auto_cfg = next(m for m in MODEL_CHAIN if m.get("provider") == "auto")
    expected = next(m for m in MODEL_CHAIN if m.get("provider") != "auto")

    resolved = cl._resolve_auto_to_concrete(auto_cfg)

    assert resolved is expected
    assert resolved.get("provider") != "auto"


def test_resolve_auto_passes_concrete_config_through_unchanged():
    concrete = {"provider": "azure_openai", "name": "crowelm-supreme", "label": "X"}
    assert cl._resolve_auto_to_concrete(concrete) is concrete
