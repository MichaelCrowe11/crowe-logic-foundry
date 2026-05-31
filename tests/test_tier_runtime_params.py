"""Reasoning-class gpt-5 deployments must send max_completion_tokens, not
max_tokens (they 400 on max_tokens and burn the budget on hidden reasoning).

gpt-5.5 is CroweLM Supreme's backend, so it must be normalized like the rest of
the gpt-5 family. Chat (non-reasoning) deployments keep classic max_tokens.
"""

from config.agent_config import tier_runtime_params


def test_gpt55_reasoning_uses_max_completion_tokens_not_max_tokens():
    params = tier_runtime_params(
        {"name": "gpt-5.5", "backend_name": "gpt-5.5", "type": "reasoning"}
    )
    assert "max_tokens" not in params
    assert "max_completion_tokens" in params
    assert isinstance(params["max_completion_tokens"], int)


def test_chat_model_keeps_classic_max_tokens():
    params = tier_runtime_params(
        {
            "name": "DeepSeek-V4-Flash",
            "backend_name": "DeepSeek-V4-Flash",
            "type": "reasoning",
        }
    )
    assert "max_completion_tokens" not in params
    assert "max_tokens" in params
