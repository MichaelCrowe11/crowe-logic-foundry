"""
Crowe-Synapse Quantum Bridge — pluggable decision points.

Any routing decision, pipeline step, or parameter selection can optionally
flow through Crowe Quantum evaluation. When quantum packages aren't installed,
all decisions use classical defaults. Zero overhead when quantum isn't active.

Uses the published crowe-quantum-core, crowe-qubit-flow, and crowe-synapse
PyPI packages from the Crowe Quantum Platform.
"""

import random
from dataclasses import dataclass, field


# Check quantum availability once at import time
_core_available = False
_qubit_flow_available = False

try:
    from crowe_quantum_core import StateVector, standard_gates
    _core_available = True
except ImportError:
    pass

try:
    from crowe_qubit_flow import Parser as QFParser, Interpreter as QFInterpreter
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
        self._qf_interpreter = QFInterpreter() if _qubit_flow_available else None

    @property
    def quantum_available(self) -> bool:
        return _core_available or _qubit_flow_available

    def status(self) -> dict:
        return {
            "available": self.quantum_available,
            "crowe_quantum_core": _core_available,
            "crowe_qubit_flow": _qubit_flow_available,
        }

    def decide(self, dp: DecisionPoint) -> str:
        """Evaluate a decision point. Uses quantum if available, classical otherwise."""
        if dp.quantum_evaluator and _core_available:
            try:
                result = self._quantum_evaluate(dp)
                if result in dp.candidates:
                    return result
            except Exception:
                pass  # fall through to classical

        if dp.weights:
            return self._weighted_choice(dp)
        return dp.classical_default

    def _quantum_evaluate(self, dp: DecisionPoint) -> str:
        """Use quantum measurement to pick a candidate.

        Creates a superposition over candidates and measures to collapse
        to a single choice — true quantum randomness when available.
        """
        import numpy as np
        n_candidates = len(dp.candidates)
        # Use enough qubits to cover all candidates
        num_qubits = max(1, int(np.ceil(np.log2(n_candidates))))
        state = StateVector(num_qubits)
        # Apply Hadamard to all qubits for uniform superposition
        h_gate = standard_gates.get_gate("H")
        for q in range(num_qubits):
            state.apply_gate(h_gate.matrix(), [q])
        # Measure and map to candidate
        outcome = state.measure()
        idx = outcome % n_candidates
        return dp.candidates[idx]

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
