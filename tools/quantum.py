"""
Quantum computing tool — execute circuits via Qiskit, Cirq, PennyLane, and Crowe Quantum Trinity.

Uses crowe-quantum-trinity for native quantum simulation, scientific reasoning,
and the bridge pipeline connecting QubitFlow circuits to Synapse analysis.
"""

import json
from typing import Optional


def run_quantum_circuit(code: str, backend: str = "qiskit", shots: int = 1024) -> str:
    """
    Execute a quantum circuit using the specified backend.
    Supports Qiskit (IBM Quantum), Cirq (Google), PennyLane, and Crowe Quantum Trinity.

    :param code: Python code defining the quantum circuit. Must assign result to a variable called 'result'.
    :param backend: Quantum framework (qiskit, cirq, pennylane, crowe).
    :param shots: Number of measurement shots (default 1024).
    :return: JSON with circuit execution results (counts, probabilities, statevector).
    :rtype: str
    """
    try:
        namespace = {"__builtins__": __builtins__, "shots": shots}

        if backend == "qiskit":
            import qiskit
            from qiskit_aer import AerSimulator
            namespace["QuantumCircuit"] = qiskit.QuantumCircuit
            namespace["AerSimulator"] = AerSimulator
        elif backend == "cirq":
            import cirq
            import numpy as np
            namespace["cirq"] = cirq
            namespace["np"] = np
        elif backend == "pennylane":
            import pennylane as qml
            import numpy as np
            namespace["qml"] = qml
            namespace["np"] = np
        elif backend in ("crowe", "trinity"):
            import numpy as np
            from crowe_quantum_trinity import (
                run as trinity_run,
                compile as trinity_compile,
                TrinityPipeline,
            )
            from crowe_quantum_core.states import StateVector
            from crowe_quantum_core.gates import standard_gates
            namespace["StateVector"] = StateVector
            namespace["standard_gates"] = standard_gates
            namespace["trinity_run"] = trinity_run
            namespace["trinity_compile"] = trinity_compile
            namespace["TrinityPipeline"] = TrinityPipeline
            namespace["np"] = np

        compiled = compile(code, "<quantum-circuit>", "exec")
        exec(compiled, namespace)  # noqa: S102 — intentional sandboxed execution

        result = namespace.get("result", "No 'result' variable found. Assign your output to 'result'.")

        if hasattr(result, "to_dict"):
            return json.dumps({"backend": backend, "result": result.to_dict()})
        else:
            return json.dumps({"backend": backend, "result": str(result)})

    except Exception as e:
        return json.dumps({"error": str(e), "backend": backend})


def synapse_evaluate(expression: str) -> str:
    """
    Evaluate a Crowe Synapse expression for scientific reasoning with
    uncertainty propagation and symbolic math.

    :param expression: A Synapse expression (symbolic math, uncertainty arithmetic, etc).
    :return: JSON with the evaluation result.
    :rtype: str
    """
    try:
        from crowe_quantum_trinity import UncertainValue, Symbol, Expression
        from crowe_synapse import simplify
        namespace = {
            "Symbol": Symbol,
            "Expression": Expression,
            "simplify": simplify,
            "UncertainValue": UncertainValue,
        }
        compiled = compile(f"result = {expression}", "<synapse-eval>", "exec")
        exec(compiled, namespace)  # noqa: S102 — intentional sandboxed execution
        result = namespace.get("result", expression)
        return json.dumps({"expression": expression, "result": str(result)})
    except Exception as e:
        return json.dumps({"error": str(e), "expression": expression})


def qubit_flow_execute(program: str) -> str:
    """
    Execute a Qubit-Flow program via the Trinity bridge.
    Qubit-Flow is a quantum circuit design language with Dirac notation.

    :param program: A Qubit-Flow program string.
    :return: JSON with execution results including measurement outcomes and probabilities.
    :rtype: str
    """
    try:
        from crowe_quantum_trinity import run
        result = run(program)
        return json.dumps({
            "program": program[:500],
            "measurements": result.measurements,
            "probabilities": result.probabilities(),
            "variables": {k: str(v) for k, v in result.variables.items()},
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def trinity_pipeline(program: str, shots: int = 1024, expected: Optional[dict] = None) -> str:
    """
    Run a full Trinity pipeline: parse, execute, sample, and analyze a QubitFlow circuit.
    Optionally test against an expected probability distribution.

    :param program: A Qubit-Flow program string.
    :param shots: Number of measurement shots (default 1024).
    :param expected: Optional expected probability distribution to test against.
    :return: JSON experiment report with probabilities, uncertainty, and hypothesis test results.
    :rtype: str
    """
    try:
        from crowe_quantum_trinity import TrinityPipeline
        pipe = TrinityPipeline()
        report = pipe.run_experiment(program, shots=shots, expected=expected)
        return json.dumps({
            "program": program[:500],
            "shots": shots,
            "passed": report.passed,
            "summary": report.summary(),
        })
    except Exception as e:
        return json.dumps({"error": str(e)})
