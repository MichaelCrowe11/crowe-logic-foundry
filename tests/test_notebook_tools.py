"""Tests for tools/notebook.py — Cortex notebook kernel-host bridge.

These never touch a real notebook host: happy paths route through
`httpx.MockTransport` by monkeypatching the module-level `_client` factory,
and error paths use a connection-refused URL (http://127.0.0.1:1).
"""

from __future__ import annotations

import json

import httpx
import pytest

import tools.notebook as nb

NB_ID = "nb-123"
CELL_ID = "cell-456"
ALL_TOOLS = [
    "notebook_create",
    "notebook_list",
    "notebook_read",
    "notebook_run",
    "notebook_edit_cell",
    "notebook_restart",
]


# ---------------------------------------------------------------------------
# Helpers — wire a MockTransport into the module's client factory
# ---------------------------------------------------------------------------


def install_transport(monkeypatch, handler):
    """Patch nb._client so every tool call goes through `handler`.

    Records each (method, path, json_body, timeout) for assertions.
    """
    calls = []

    def _handler(request: httpx.Request) -> httpx.Response:
        body = None
        if request.content:
            body = json.loads(request.content)
        calls.append(
            {
                "method": request.method,
                "path": request.url.path,
                "json": body,
            }
        )
        return handler(request)

    transport = httpx.MockTransport(_handler)
    timeouts = []

    def _fake_client(timeout):
        timeouts.append(timeout)
        return httpx.Client(transport=transport, timeout=timeout)

    monkeypatch.setattr(nb, "_client", _fake_client)
    return calls, timeouts


@pytest.fixture()
def enabled(monkeypatch):
    monkeypatch.setenv("CORTEX_NOTEBOOK_URL", "http://127.0.0.1:9999")


# ---------------------------------------------------------------------------
# Registration gate
# ---------------------------------------------------------------------------


class TestRegisterGate:
    def test_noop_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("CORTEX_NOTEBOOK_URL", raising=False)
        target = set()
        assert nb.register(target) == []
        assert target == set()

    def test_noop_when_env_empty(self, monkeypatch):
        monkeypatch.setenv("CORTEX_NOTEBOOK_URL", "")
        target = set()
        assert nb.register(target) == []
        assert target == set()

    def test_registers_all_six_when_env_set(self, enabled):
        target = set()
        names = nb.register(target)
        assert sorted(names) == sorted(ALL_TOOLS)
        assert len(target) == 6
        assert all(callable(fn) for fn in target)


# ---------------------------------------------------------------------------
# System-prompt addendum
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    def test_empty_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("CORTEX_NOTEBOOK_URL", raising=False)
        assert nb.system_prompt() == ""

    def test_addendum_when_env_set(self, enabled):
        assert nb.system_prompt() == nb.SYSTEM_PROMPT_ADDENDUM


# ---------------------------------------------------------------------------
# Error paths — host unreachable, never raise
# ---------------------------------------------------------------------------


class TestUnreachableHost:
    @pytest.fixture(autouse=True)
    def refused(self, monkeypatch):
        # Port 1 on loopback: connection refused fast, no real host involved.
        monkeypatch.setenv("CORTEX_NOTEBOOK_URL", "http://127.0.0.1:1")

    @pytest.mark.parametrize(
        "call",
        [
            lambda: nb.notebook_create("scratch"),
            lambda: nb.notebook_list(),
            lambda: nb.notebook_read(NB_ID),
            lambda: nb.notebook_read(NB_ID, include_outputs=False),
            lambda: nb.notebook_run(NB_ID, "print(1)"),
            lambda: nb.notebook_edit_cell(NB_ID, CELL_ID, "print(2)"),
            lambda: nb.notebook_restart(NB_ID),
        ],
    )
    def test_returns_json_error(self, call):
        out = call()
        data = json.loads(out)
        assert "error" in data
        assert "notebook host unreachable" in data["error"]
        assert data["hint"] == "the Cortex notebook host may not be running"


# ---------------------------------------------------------------------------
# Happy paths via MockTransport
# ---------------------------------------------------------------------------


class TestHappyPaths:
    def test_create_posts_name(self, enabled, monkeypatch):
        payload = {"id": NB_ID, "name": "analysis", "path": "/nbs/analysis.ipynb"}
        calls, _ = install_transport(
            monkeypatch, lambda req: httpx.Response(200, json=payload)
        )
        out = nb.notebook_create("analysis")
        assert json.loads(out) == payload
        assert calls == [
            {"method": "POST", "path": "/v1/notebooks", "json": {"name": "analysis"}}
        ]

    def test_list_gets_collection(self, enabled, monkeypatch):
        payload = {"notebooks": [{"id": NB_ID}]}
        calls, _ = install_transport(
            monkeypatch, lambda req: httpx.Response(200, json=payload)
        )
        out = nb.notebook_list()
        assert json.loads(out) == payload
        assert calls[0]["method"] == "GET"
        assert calls[0]["path"] == "/v1/notebooks"

    def test_run_posts_cell_to_right_path(self, enabled, monkeypatch):
        result = {
            "cell_id": CELL_ID,
            "status": "ok",
            "execution_count": 1,
            "output_text": "1\n",
            "mime_summary": [],
            "truncated": False,
        }
        calls, timeouts = install_transport(
            monkeypatch, lambda req: httpx.Response(200, json=result)
        )
        out = nb.notebook_run(NB_ID, "print(1)")
        assert json.loads(out) == result
        assert calls == [
            {
                "method": "POST",
                "path": f"/v1/notebooks/{NB_ID}/cells",
                "json": {"cell_type": "code", "source": "print(1)", "timeout": 120},
            }
        ]
        # HTTP client outlives the cell: timeout + 30.
        assert timeouts == [150]

    def test_run_passes_custom_timeout_and_cell_type(self, enabled, monkeypatch):
        calls, timeouts = install_transport(
            monkeypatch, lambda req: httpx.Response(200, json={"status": "ok"})
        )
        nb.notebook_run(NB_ID, "# notes", cell_type="markdown", timeout=300)
        assert calls[0]["json"] == {
            "cell_type": "markdown",
            "source": "# notes",
            "timeout": 300,
        }
        assert timeouts == [330]

    def test_edit_cell_puts_to_cell_path(self, enabled, monkeypatch):
        calls, timeouts = install_transport(
            monkeypatch, lambda req: httpx.Response(200, json={"status": "ok"})
        )
        nb.notebook_edit_cell(NB_ID, CELL_ID, "print(2)", timeout=60)
        assert calls == [
            {
                "method": "PUT",
                "path": f"/v1/notebooks/{NB_ID}/cells/{CELL_ID}",
                "json": {"source": "print(2)", "timeout": 60},
            }
        ]
        assert timeouts == [90]

    def test_restart_posts(self, enabled, monkeypatch):
        calls, _ = install_transport(
            monkeypatch, lambda req: httpx.Response(200, json={"restarted": True})
        )
        out = nb.notebook_restart(NB_ID)
        assert json.loads(out) == {"restarted": True}
        assert calls[0]["method"] == "POST"
        assert calls[0]["path"] == f"/v1/notebooks/{NB_ID}/restart"

    def test_read_keeps_outputs_by_default(self, enabled, monkeypatch):
        doc = {
            "id": NB_ID,
            "cells": [
                {"cell_id": CELL_ID, "source": "print(1)", "outputs": ["1\n"]},
            ],
        }
        calls, _ = install_transport(
            monkeypatch, lambda req: httpx.Response(200, json=doc)
        )
        out = nb.notebook_read(NB_ID)
        assert json.loads(out) == doc
        assert calls[0]["method"] == "GET"
        assert calls[0]["path"] == f"/v1/notebooks/{NB_ID}"

    def test_read_strips_outputs_when_disabled(self, enabled, monkeypatch):
        doc = {
            "id": NB_ID,
            "cells": [
                {"cell_id": "c1", "source": "print(1)", "outputs": ["1\n"]},
                {"cell_id": "c2", "source": "# md"},
            ],
        }
        install_transport(monkeypatch, lambda req: httpx.Response(200, json=doc))
        out = nb.notebook_read(NB_ID, include_outputs=False)
        data = json.loads(out)
        assert data["id"] == NB_ID
        assert [c["cell_id"] for c in data["cells"]] == ["c1", "c2"]
        assert all("outputs" not in c for c in data["cells"])

    def test_non_2xx_wrapped_as_error(self, enabled, monkeypatch):
        install_transport(
            monkeypatch,
            lambda req: httpx.Response(409, text="cell already running"),
        )
        out = nb.notebook_run(NB_ID, "print(1)")
        data = json.loads(out)
        assert data == {"error": "cell already running", "status_code": 409}
