"""Tests for crowe_synapse_engine.quantum_bridge — pluggable quantum decisions."""

import pytest
from crowe_synapse_engine.quantum_bridge import DecisionPoint, QuantumBridge


@pytest.fixture
def bridge():
    return QuantumBridge()


class TestDecisionPoint:
    def test_classical_default_when_no_evaluator(self, bridge):
        dp = DecisionPoint(
            name="test_route",
            candidates=["code", "music", "research"],
            classical_default="code",
            quantum_evaluator=None,
        )
        result = bridge.decide(dp)
        assert result == "code"

    def test_classical_default_when_quantum_unavailable(self, bridge):
        dp = DecisionPoint(
            name="test_route",
            candidates=["code", "music"],
            classical_default="music",
            quantum_evaluator="synapse.route(candidates, tension=0.5)",
        )
        # Even with an evaluator string, if synapse-lang isn't importable
        # in the test environment, it should fall back to classical
        result = bridge.decide(dp)
        assert result in dp.candidates

    def test_decide_returns_valid_candidate(self, bridge):
        dp = DecisionPoint(
            name="test",
            candidates=["a", "b", "c"],
            classical_default="b",
        )
        result = bridge.decide(dp)
        assert result in dp.candidates


class TestQuantumAvailability:
    def test_quantum_available_is_bool(self, bridge):
        assert isinstance(bridge.quantum_available, bool)

    def test_bridge_reports_status(self, bridge):
        status = bridge.status()
        assert "available" in status
        assert isinstance(status["available"], bool)
        assert "synapse_lang" in status
        assert "qubit_flow" in status


class TestWeightedDecision:
    def test_weighted_classical_selection(self, bridge):
        dp = DecisionPoint(
            name="weighted",
            candidates=["a", "b", "c"],
            classical_default="a",
            weights={"a": 0.7, "b": 0.2, "c": 0.1},
        )
        # With classical fallback using weights, result should still be valid
        results = {bridge.decide(dp) for _ in range(20)}
        assert results.issubset({"a", "b", "c"})
