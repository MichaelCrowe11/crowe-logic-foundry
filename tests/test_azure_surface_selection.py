"""Reasoning-class Azure tiers must use the Responses API surface.

Azure rejects ``reasoning_effort`` + function tools on /v1/chat/completions
("Please use /v1/responses instead"). The provider class is chosen by
``model_cfg["surface"] == "responses"``, but the reasoning tiers in the chain
(gpt-5.5, DeepSeek-R1, Kimi, grok-reasoning, ...) don't carry that flag — so
they fall to the chat-completions provider and 400. The surface must be
inferred from the backend, not rely on a hand-set flag.
"""

import cli.crowe_logic as cl


def test_reasoning_backend_without_flag_uses_responses():
    # crowelm-supreme = gpt-5.5, a reasoning backend, with no surface flag.
    cfg = {"name": "crowelm-supreme", "backend_name": "gpt-5.5"}
    assert cl._azure_surface_is_responses(cfg) is True


def test_explicit_responses_flag_is_honored():
    cfg = {"name": "x", "backend_name": "gpt-4o", "surface": "responses"}
    assert cl._azure_surface_is_responses(cfg) is True


def test_non_reasoning_backend_uses_chat_completions():
    cfg = {"name": "y", "backend_name": "gpt-4o-mini"}
    assert cl._azure_surface_is_responses(cfg) is False
