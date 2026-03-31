"""
Crowe-Synapse Quantum Bridge — pluggable decision points.

Any routing decision, pipeline step, or parameter selection can optionally
flow through Synapse-Lang or Qubit-Flow quantum evaluation. When quantum
packages aren't installed, all decisions use classical defaults. Zero overhead
when quantum isn't active.
"""

import random
from dataclasses import dataclass, field


# Check quantum availability once at import time
_synapse_available = False
_qubit_flow_available = False

try:
    from synapse_lang import SynapseLang
    _synapse_available = True
except ImportError:
    pass

try:
    from qubit_flow_lang import QubitFlowInterpreter
    _qubit_flow_available = True
except ImportError:
    pass


@dataclass
class DecisionPoint:
    name: str
    candidates: list[str]
    classical_default: str
    quantum_evaluator: str | None = None
    weights: dict[str, float] = field(default_factory=dict)


class QuantumBridge:
    def __init__(self):
        self._synapse = SynapseLang() if _synapse_available else None
        self._qubit_flow = QubitFlowInterpreter() if _qubit_flow_available else None

    @property
    def quantum_available(self) -> bool:
        return _synapse_available or _qubit_flow_available

    def status(self) -> dict:
        return {
            "available": self.quantum_available,
            "synapse_lang": _synapse_available,
            "qubit_flow": _qubit_flow_available,
        }

    def decide(self, dp: DecisionPoint) -> str:
        """Evaluate a decision point. Uses quantum if available, classical otherwise."""
        # Try quantum evaluation first
        if dp.quantum_evaluator and self._synapse:
            try:
                result = self._quantum_evaluate(dp)
                if result in dp.candidates:
                    return result
            except Exception:
                pass  # fall through to classical

        # Classical: use weights if provided, otherwise return default
        if dp.weights:
            return self._weighted_choice(dp)
        return dp.classical_default

    def _quantum_evaluate(self, dp: DecisionPoint) -> str:
        """Run a Synapse-Lang expression to pick a candidate."""
        result = self._synapse.evaluate(dp.quantum_evaluator)
        return str(result)

    def _weighted_choice(self, dp: DecisionPoint) -> str:
        """Weighted random selection from candidates using provided weights."""
        candidates = []
        weights = []
        for c in dp.candidates:
            candidates.append(c)
            weights.append(dp.weights.get(c, 0.0))
        total = sum(weights)
        if total == 0:
            return dp.classical_default
        return random.choices(candidates, weights=weights, k=1)[0]
