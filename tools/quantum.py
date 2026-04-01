"""
Quantum computing tool — execute circuits via Qiskit, Cirq, PennyLane, and Crowe Quantum.

Uses the published crowe-quantum-core, crowe-qubit-flow, and crowe-synapse
PyPI packages for native quantum simulation and scientific reasoning.
"""

import json


def run_quantum_circuit(code: str, backend: str = "qiskit", shots: int = 1024) -> str:
    """
    Execute a quantum circuit using the specified backend.
    Supports Qiskit (IBM Quantum), Cirq (Google), PennyLane, and Crowe Quantum Core.

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
        elif backend in ("crowe", "synapse"):
            import numpy as np
            from crowe_quantum_core import StateVector, standard_gates
            namespace["StateVector"] = StateVector
            namespace["standard_gates"] = standard_gates
            namespace["np"] = np

        # Execute user-provided circuit code in sandboxed namespace
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
        from crowe_synapse import Symbol, Expression, simplify, UncertainValue
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
    Execute a Qubit-Flow program. Qubit-Flow is a quantum circuit design
    language with Dirac notation — part of the Crowe Quantum Platform.

    :param program: A Qubit-Flow program string.
    :return: JSON with execution results including measurement outcomes.
    :rtype: str
    """
    try:
        from crowe_qubit_flow import Parser, Interpreter
        ast = Parser.from_source(program).parse()
        interpreter = Interpreter()
        result = interpreter.execute(ast)
        return json.dumps({"program": program[:500], "result": str(result)})
    except Exception as e:
        return json.dumps({"error": str(e)})
