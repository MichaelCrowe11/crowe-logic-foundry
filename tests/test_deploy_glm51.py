"""Tests for the GLM 5.1 Azure ML deployment helper."""

from __future__ import annotations

from types import SimpleNamespace

from scripts import deploy_glm51 as deploy_mod


def test_cmd_key_prints_openai_compatible_v1_endpoint(monkeypatch, capsys):
    fake_ml = SimpleNamespace(
        online_endpoints=SimpleNamespace(
            get=lambda _: SimpleNamespace(
                scoring_uri="https://crowelm-dense-glm51.eastus.inference.ml.azure.com/score"
            ),
            get_keys=lambda _: SimpleNamespace(primary_key="glm-test-key"),
        )
    )
    monkeypatch.setattr(deploy_mod, "_get_ml_client", lambda: fake_ml)

    deploy_mod.cmd_key(SimpleNamespace())

    output = capsys.readouterr().out
    assert "AZURE_GLM51_ENDPOINT=https://crowelm-dense-glm51.eastus.inference.ml.azure.com/v1" in output
    assert "AZURE_GLM51_API_KEY=glm-test-key" in output
