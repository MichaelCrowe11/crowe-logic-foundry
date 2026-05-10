#!/usr/bin/env python3
# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
"""
Smoke test for the DeepSeek V4 + direct-Anthropic registry swap.

Verifies that:
  1. config/models.extra.json parses and merges into MODEL_CHAIN.
  2. The new CroweLM Sage / Loom / Sonnet aliases resolve via
     resolve_model_config() to entries with the expected provider and
     backend_name.
  3. AnthropicProvider can be constructed with an empty endpoint and an
     api.anthropic.com endpoint without crashing (direct-API path).
  4. Optional: if the local Ollama daemon is reachable, probe each V4
     :cloud model via check_cloud_model_availability. The test does not
     fail on network or paywall errors; it only fails when the registry
     surface itself is wrong.

Run from repo root:
    .venv/bin/python scripts/smoke_v4_cloud_swap.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))
    icon = "PASS" if ok else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{icon}]  {name}{suffix}")


def expect_resolves(selector: str, want_provider: str, want_backend: str) -> dict | None:
    from config.agent_config import resolve_model_config

    cfg = resolve_model_config(selector)
    if cfg is None:
        check(f"resolve '{selector}'", False, "no match in MODEL_CHAIN")
        return None
    label = cfg.get("label", "?")
    provider = cfg.get("provider", "?")
    backend = cfg.get("backend_name", "?")
    if provider != want_provider:
        check(f"resolve '{selector}'", False,
              f"provider={provider} (want {want_provider}); label={label}")
        return cfg
    if backend != want_backend:
        check(f"resolve '{selector}'", False,
              f"backend_name={backend} (want {want_backend}); label={label}")
        return cfg
    check(f"resolve '{selector}'", True, f"-> {label} via {provider}")
    return cfg


def main() -> int:
    print("=" * 64)
    print("CROWE LOGIC FOUNDRY: V4 + DIRECT-ANTHROPIC SMOKE TEST")
    print("=" * 64)

    print("\n[1] models.extra.json + MODEL_CHAIN merge")
    try:
        from config.agent_config import MODEL_CHAIN
        labels = {cfg.get("label") for cfg in MODEL_CHAIN}
        check("MODEL_CHAIN populated", len(MODEL_CHAIN) > 0,
              f"{len(MODEL_CHAIN)} tiers")
        for required in ("CroweLM Sage", "CroweLM Loom", "CroweLM Sonnet"):
            check(f"chain contains '{required}'", required in labels)
    except Exception as exc:
        check("MODEL_CHAIN import", False, f"{type(exc).__name__}: {exc}")
        return 2

    print("\n[2] alias resolution -> provider + backend_name")
    sage = expect_resolves("sage", "ollama", "deepseek-v4-flash:cloud")
    loom = expect_resolves("loom", "ollama", "deepseek-v4-pro:cloud")
    sonnet = expect_resolves("sonnet", "anthropic", "claude-sonnet-4-6")
    expect_resolves("v4-flash", "ollama", "deepseek-v4-flash:cloud")
    expect_resolves("v4-pro", "ollama", "deepseek-v4-pro:cloud")
    expect_resolves("CroweLM Sonnet", "anthropic", "claude-sonnet-4-6")

    print("\n[3] system prompts wired")
    for cfg, name in ((sage, "Sage"), (loom, "Loom"), (sonnet, "Sonnet")):
        if cfg is None:
            continue
        prompt = (cfg.get("prompt") or cfg.get("system_prompt") or "").strip()
        check(f"{name} system prompt present", bool(prompt),
              f"{len(prompt)} chars")
        if prompt:
            check(f"{name} prompt has identity rule",
                  "Identity rules" in prompt or "Not a standalone product" in prompt)

    print("\n[4] AnthropicProvider direct-API construction")
    try:
        from providers.anthropic import AnthropicProvider
    except Exception as exc:
        check("import AnthropicProvider", False, f"{type(exc).__name__}: {exc}")
    else:
        check("import AnthropicProvider", True)
        for endpoint, label in (
            ("", "empty endpoint -> SDK default"),
            ("https://api.anthropic.com", "explicit api.anthropic.com"),
            ("https://my-azure.openai.azure.com", "Azure endpoint still appends /anthropic"),
        ):
            try:
                inst = AnthropicProvider(
                    model="claude-sonnet-4-6",
                    system_instructions="probe",
                    endpoint=endpoint,
                    api_key="sk-ant-probe",
                    label="probe",
                )
                base_url = str(getattr(inst.client, "base_url", "")).rstrip("/")
                if endpoint and "azure" in endpoint:
                    ok = base_url.endswith("/anthropic")
                    check(f"{label}", ok, f"base_url={base_url}")
                else:
                    ok = "api.anthropic.com" in base_url
                    check(f"{label}", ok, f"base_url={base_url}")
            except Exception as exc:
                check(f"{label}", False, f"{type(exc).__name__}: {exc}")

    print("\n[5] Ollama Cloud reachability (optional)")
    try:
        from providers.ollama import check_cloud_model_availability
        import os
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        for model in ("deepseek-v4-flash:cloud", "deepseek-v4-pro:cloud"):
            res = check_cloud_model_availability(model, base_url=base_url)
            if res.ok:
                check(f"probe {model}", True, "ok")
            elif res.paywalled:
                check(f"probe {model}", True, "paywalled (account upgrade required)")
            elif res.reason and "daemon not reachable" in res.reason:
                check(f"probe {model}", True, "skipped (daemon offline)")
            else:
                # Treat any other reachability failure as informational, not fatal:
                # the registry surface is what this test gates on.
                check(f"probe {model}", True,
                      f"informational: {res.reason}")
    except Exception as exc:
        check("ollama probe", True, f"skipped: {type(exc).__name__}: {exc}")

    print()
    print("=" * 64)
    failed = [c for c in CHECKS if not c[1]]
    print(f"RESULT: {len(CHECKS) - len(failed)}/{len(CHECKS)} passed"
          + (f"  ({len(failed)} failed)" if failed else ""))
    print("=" * 64)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
