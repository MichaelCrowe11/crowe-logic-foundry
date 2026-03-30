"""
Quantum computing tool — execute circuits via Qiskit, Cirq, PennyLane, and Synapse.
"""

import json


def run_quantum_circuit(code: str, backend: str = "qiskit", shots: int = 1024) -> str:
    """
    Execute a quantum circuit using the specified backend.
    Supports Qiskit (IBM Quantum), Cirq (Google), PennyLane, and Synapse-Lang.

    :param code: Python code defining the quantum circuit. Must assign result to a variable called 'result'.
    :param backend: Quantum framework (qiskit, cirq, pennylane, synapse).
    :param shots: Number of measurement shots (default 1024).
    :return: JSON with circuit execution results (counts, probabilities, statevector).
    :rtype: str
    """
    try:
        # Create a safe execution namespace with quantum imports
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
        elif backend == "synapse":
            import synapse_lang
            namespace["synapse_lang"] = synapse_lang

        exec(code, namespace)

        result = namespace.get("result", "No 'result' variable found. Assign your output to 'result'.")

        # Serialize the result
        if hasattr(result, "to_dict"):
            return json.dumps({"backend": backend, "result": result.to_dict()})
        else:
            return json.dumps({"backend": backend, "result": str(result)})

    except Exception as e:
        return json.dumps({"error": str(e), "backend": backend})


def synapse_evaluate(expression: str) -> str:
    """
    Evaluate a Synapse-Lang expression. Synapse is a quantum-classical
    hybrid programming language created by Michael Crowe.

    :param expression: A Synapse-Lang expression or program.
    :return: JSON with the evaluation result.
    :rtype: str
    """
    try:
        from synapse_lang import SynapseLang
        sl = SynapseLang()
        result = sl.evaluate(expression)
        return json.dumps({"expression": expression, "result": str(result)})
    except Exception as e:
        return json.dumps({"error": str(e), "expression": expression})


def qubit_flow_execute(program: str) -> str:
    """
    Execute a Qubit-Flow program. Qubit-Flow is a quantum circuit design
    language that's part of the Quantum Trinity (with Synapse-Lang).

    :param program: A Qubit-Flow program string.
    :return: JSON with execution results.
    :rtype: str
    """
    try:
        from qubit_flow_lang import QubitFlowInterpreter
        interpreter = QubitFlowInterpreter()
        result = interpreter.run(program)
        return json.dumps({"program": program[:500], "result": str(result)})
    except Exception as e:
        return json.dumps({"error": str(e)})
