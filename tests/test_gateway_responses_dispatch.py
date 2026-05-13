"""Regression tests for the gateway's Azure surface dispatch.

Before 2026-05-13 the non-streaming ``/api/gateway/chat`` path always
called ``chat.completions.create()`` even for models marked
``surface: responses``, which 400s with "Invalid model" against
deployments that only expose ``/v1/responses`` (e.g. gpt-5.4-pro). These
tests pin the dispatch so a future refactor can't reintroduce the bug.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def fake_azure_env(monkeypatch):
    """Provide fake credentials so the credential checks in _call_provider
    pass before the test gets a chance to inspect dispatch."""
    monkeypatch.setenv("AZURE_CORE_ENDPOINT", "https://fake-core.openai.azure.com")
    monkeypatch.setenv("AZURE_CORE_API_KEY", "fake-core-key")
    monkeypatch.setenv("AZURE_8909_ENDPOINT", "https://fake-8909.openai.azure.com")
    monkeypatch.setenv("AZURE_8909_API_KEY", "fake-8909-key")


def _pick_chat_completions_model_name() -> str:
    """Return a model name from MODEL_CHAIN with provider=azure_openai and
    no responses surface. Skips the test if no such model exists."""
    from config.agent_config import MODEL_CHAIN

    for entry in MODEL_CHAIN:
        if (
            entry.get("provider") == "azure_openai"
            and entry.get("surface") != "responses"
        ):
            return entry["name"]
    pytest.skip("No azure_openai chat-completions model in MODEL_CHAIN")


@pytest.mark.asyncio
async def test_responses_surface_uses_responses_api():
    """Models with surface=responses route to AzureResponsesProvider and
    call client.responses.create, never chat.completions.create."""
    from config.agent_config import resolve_model_config
    from control_plane.gateway import _call_provider

    cfg = resolve_model_config("gpt-5.4-pro-managed")
    assert cfg is not None, "gpt-5.4-pro-managed must exist in MODEL_CHAIN"
    assert cfg.get("surface") == "responses", (
        "gpt-5.4-pro-managed must keep surface=responses for this test to exercise the bug"
    )

    fake_response = MagicMock()
    fake_response.output_text = "responses reply"
    fake_response.usage.input_tokens = 11
    fake_response.usage.output_tokens = 7

    with (
        patch("providers.azure_openai.AzureResponsesProvider") as mock_responses_cls,
        patch("providers.azure_openai.AzureOpenAIProvider") as mock_chat_cls,
    ):
        mock_inst = mock_responses_cls.return_value
        mock_inst.client.responses.create.return_value = fake_response
        mock_inst.model = cfg.get("backend_name", cfg["name"])

        content, prompt_tokens, completion_tokens = await _call_provider(
            model="gpt-5.4-pro-managed",
            messages=[{"role": "user", "content": "hello"}],
        )

    mock_responses_cls.assert_called_once()
    mock_chat_cls.assert_not_called()
    mock_inst.client.responses.create.assert_called_once()
    mock_inst.client.chat.completions.create.assert_not_called()
    assert content == "responses reply"
    assert prompt_tokens == 11
    assert completion_tokens == 7


@pytest.mark.asyncio
async def test_responses_call_uses_input_and_instructions_shape():
    """The Responses API call must pass `input` (not `messages`) and
    `instructions` (system prompt as top-level field)."""
    from control_plane.gateway import _call_provider

    fake_response = MagicMock()
    fake_response.output_text = "ok"
    fake_response.usage.input_tokens = 1
    fake_response.usage.output_tokens = 1

    with (
        patch("providers.azure_openai.AzureResponsesProvider") as mock_responses_cls,
        patch("providers.azure_openai.AzureOpenAIProvider"),
    ):
        mock_inst = mock_responses_cls.return_value
        mock_inst.client.responses.create.return_value = fake_response
        mock_inst.model = "gpt-5.4-pro"

        await _call_provider(
            model="gpt-5.4-pro-managed",
            messages=[{"role": "user", "content": "what is mycelium"}],
            max_tokens=500,
            temperature=0.3,
        )

    call_kwargs = mock_inst.client.responses.create.call_args.kwargs
    assert "input" in call_kwargs, "Responses API requires `input`, not `messages`"
    assert "messages" not in call_kwargs, (
        "Chat-completions shape must not leak into Responses call"
    )
    assert call_kwargs.get("instructions"), (
        "Responses API requires `instructions` field"
    )
    assert call_kwargs.get("max_output_tokens") == 500, (
        "max_tokens must map to max_output_tokens for Responses surface"
    )
    assert call_kwargs.get("temperature") == 0.3


@pytest.mark.asyncio
async def test_chat_surface_unchanged():
    """Models without surface=responses keep using AzureOpenAIProvider and
    chat.completions.create."""
    from control_plane.gateway import _call_provider

    model_name = _pick_chat_completions_model_name()

    fake_response = MagicMock()
    fake_response.choices[0].message.content = "chat reply"
    fake_response.usage.prompt_tokens = 4
    fake_response.usage.completion_tokens = 6

    with (
        patch("providers.azure_openai.AzureResponsesProvider") as mock_responses_cls,
        patch("providers.azure_openai.AzureOpenAIProvider") as mock_chat_cls,
    ):
        mock_inst = mock_chat_cls.return_value
        mock_inst.client.chat.completions.create.return_value = fake_response
        mock_inst.model = model_name

        content, prompt_tokens, completion_tokens = await _call_provider(
            model=model_name,
            messages=[{"role": "user", "content": "hi"}],
        )

    mock_chat_cls.assert_called_once()
    mock_responses_cls.assert_not_called()
    mock_inst.client.chat.completions.create.assert_called_once()
    assert content == "chat reply"
    assert prompt_tokens == 4
    assert completion_tokens == 6


@pytest.mark.asyncio
async def test_responses_output_text_fallback_walks_output():
    """When response.output_text is empty (older SDK shape), fall back to
    walking response.output for the text content."""
    from control_plane.gateway import _call_provider

    text_piece = MagicMock()
    text_piece.text = "from output walk"
    message_item = MagicMock()
    message_item.type = "message"
    message_item.content = [text_piece]
    fake_response = MagicMock()
    fake_response.output_text = ""
    fake_response.output = [message_item]
    fake_response.usage.input_tokens = 2
    fake_response.usage.output_tokens = 3

    with (
        patch("providers.azure_openai.AzureResponsesProvider") as mock_responses_cls,
        patch("providers.azure_openai.AzureOpenAIProvider"),
    ):
        mock_inst = mock_responses_cls.return_value
        mock_inst.client.responses.create.return_value = fake_response
        mock_inst.model = "gpt-5.4-pro"

        content, prompt_tokens, completion_tokens = await _call_provider(
            model="gpt-5.4-pro-managed",
            messages=[{"role": "user", "content": "hi"}],
        )

    assert content == "from output walk"
    assert prompt_tokens == 2
    assert completion_tokens == 3
