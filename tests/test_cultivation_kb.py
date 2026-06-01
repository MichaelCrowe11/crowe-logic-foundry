import json

import tools.cultivation_kb as ckb
from tools.cultivation_kb import _parse_kb_hits, crowe_knowledge_base

# A real streamable-HTTP MCP response: SSE-framed JSON-RPC whose result.content
# carries a JSON string of corpus hits.
SSE_FIXTURE = (
    "event: message\n"
    'data: {"result":{"content":[{"type":"text","text":'
    '"{\\"hits\\":[{\\"title\\":\\"Lion\'s Mane SOP\\",\\"similarity\\":0.70,'
    '\\"content\\":\\"68F target\\",\\"tags\\":[\\"pdf\\"]}]}"}]},'
    '"jsonrpc":"2.0","id":1}\n'
)


def test_parse_kb_hits_unwraps_sse_jsonrpc_content():
    hits = _parse_kb_hits(SSE_FIXTURE)
    assert len(hits) == 1
    assert hits[0]["title"] == "Lion's Mane SOP"
    assert hits[0]["similarity"] == 0.70
    assert "68F" in hits[0]["content"]


def test_parse_kb_hits_empty_on_garbage():
    assert _parse_kb_hits("not an sse stream") == []
    assert _parse_kb_hits("") == []


def test_crowe_knowledge_base_returns_hits(monkeypatch):
    """The tool POSTs a tools/call to the MCP endpoint and returns parsed hits."""
    sent = {}

    class FakeResp:
        text = SSE_FIXTURE

        def raise_for_status(self):
            pass

    def fake_request(method, url, **kwargs):
        sent["method"] = method
        sent["url"] = url
        sent["body"] = kwargs.get("json") or json.loads(kwargs.get("content", "{}"))
        return FakeResp()

    monkeypatch.setattr(ckb.httpx, "request", fake_request)
    monkeypatch.setenv("CROWE_MYCOLOGY_MCP_TOKEN", "clk_test")
    out = json.loads(crowe_knowledge_base("lions mane temperature", limit=3))
    assert out["hits"][0]["title"] == "Lion's Mane SOP"
    # it must call tools/call for queryKnowledgeBase with our query+limit
    assert sent["body"]["method"] == "tools/call"
    assert sent["body"]["params"]["name"] == "queryKnowledgeBase"
    assert sent["body"]["params"]["arguments"]["query"] == "lions mane temperature"
    assert sent["body"]["params"]["arguments"]["limit"] == 3


def test_crowe_knowledge_base_errors_without_token(monkeypatch):
    monkeypatch.delenv("CROWE_MYCOLOGY_MCP_TOKEN", raising=False)
    out = json.loads(crowe_knowledge_base("anything"))
    assert "error" in out
