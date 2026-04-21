#!/usr/bin/env python3
"""
IBM Quantum Music MCP Server.

Exposes tools for turning qubit measurements and OpenQASM circuits into
melodies and chord progressions. Uses IBM Quantum hardware when configured and
falls back to local Aer simulation when IBM credentials are unavailable.
"""

import json
import math
import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "crowe-logic-quantum-music",
    instructions=(
        "Compose musical phrases and harmonic material from qubit measurements, "
        "OpenQASM circuits, and IBM Quantum backends."
    ),
)


NOTE_TO_SEMITONE = {
    "C": 0,
    "C#": 1,
    "DB": 1,
    "D": 2,
    "D#": 3,
    "EB": 3,
    "E": 4,
    "F": 5,
    "F#": 6,
    "GB": 6,
    "G": 7,
    "G#": 8,
    "AB": 8,
    "A": 9,
    "A#": 10,
    "BB": 10,
    "B": 11,
}

SEMITONE_TO_NOTE = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

SCALE_INTERVALS = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
    "dorian": [0, 2, 3, 5, 7, 9, 10],
    "mixolydian": [0, 2, 4, 5, 7, 9, 10],
    "pentatonic": [0, 3, 5, 7, 10],
    "blues": [0, 3, 5, 6, 7, 10],
}

TRIAD_BY_MODE = {
    "major": [0, 4, 7],
    "minor": [0, 3, 7],
    "dorian": [0, 3, 7],
    "mixolydian": [0, 4, 7],
    "pentatonic": [0, 3, 7],
    "blues": [0, 3, 6],
}


def _load_qiskit() -> dict:
    try:
        from qiskit import QuantumCircuit, transpile
    except Exception as exc:
        return {"available": False, "error": str(exc)}

    components = {
        "available": True,
        "QuantumCircuit": QuantumCircuit,
        "transpile": transpile,
        "error": "",
    }

    try:
        from qiskit_aer import AerSimulator

        components["AerSimulator"] = AerSimulator
    except Exception as exc:
        components["AerSimulator"] = None
        components["aer_error"] = str(exc)

    try:
        from qiskit_ibm_runtime import QiskitRuntimeService

        components["QiskitRuntimeService"] = QiskitRuntimeService
    except Exception as exc:
        components["QiskitRuntimeService"] = None
        components["ibm_error"] = str(exc)

    return components


def _has_ibm_credentials() -> bool:
    return bool(os.environ.get("IBM_QUANTUM_TOKEN"))


def _status_payload() -> dict:
    qiskit = _load_qiskit()
    return {
        "qiskit_available": qiskit.get("available", False),
        "aer_available": bool(qiskit.get("AerSimulator")),
        "ibm_runtime_available": bool(qiskit.get("QiskitRuntimeService")),
        "ibm_credentials_present": _has_ibm_credentials(),
        "default_backend": "ibm" if _has_ibm_credentials() and qiskit.get("QiskitRuntimeService") else "aer",
        "ibm_channel": os.environ.get("IBM_QUANTUM_CHANNEL", "ibm_quantum"),
        "configured_backend": os.environ.get("IBM_QUANTUM_BACKEND", "auto"),
    }


def _normalize_root(root: str) -> str:
    normalized = root.strip().upper()
    if normalized not in NOTE_TO_SEMITONE:
        raise ValueError(f"Unsupported root note: {root}")
    return normalized


def _normalize_mode(mode: str) -> str:
    normalized = mode.strip().lower()
    if normalized not in SCALE_INTERVALS:
        raise ValueError(f"Unsupported mode: {mode}")
    return normalized


def _midi_number(root: str, octave: int, interval: int) -> int:
    return (octave + 1) * 12 + NOTE_TO_SEMITONE[root] + interval


def _note_name(midi_note: int) -> str:
    octave = (midi_note // 12) - 1
    return f"{SEMITONE_TO_NOTE[midi_note % 12]}{octave}"


def _duration_cycle() -> list[float]:
    return [1.0, 0.5, 0.5, 1.5, 0.75, 0.25, 1.0, 0.5]


def _expanded_measurements(counts: dict[str, int], total_steps: int) -> list[str]:
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    if not ordered:
        return []

    total = sum(max(count, 0) for _, count in ordered) or 1
    expanded = []
    for bitstring, count in ordered:
        copies = max(1, round((count / total) * total_steps))
        expanded.extend([bitstring] * copies)

    while len(expanded) < total_steps:
        expanded.extend(bitstring for bitstring, _ in ordered)

    return expanded[:total_steps]


def _counts_to_melody(counts: dict[str, int], root: str, mode: str, steps: int, octave: int) -> list[dict]:
    scale = SCALE_INTERVALS[mode]
    sequence = _expanded_measurements(counts, steps)
    durations = _duration_cycle()
    total = sum(counts.values()) or 1
    events = []

    for index, bitstring in enumerate(sequence):
        value = int(bitstring, 2)
        degree = value % len(scale)
        octave_shift = (value // len(scale)) % 3
        midi_note = _midi_number(root, octave + octave_shift, scale[degree])
        probability = counts.get(bitstring, 0) / total
        events.append(
            {
                "step": index,
                "bitstring": bitstring,
                "midi": midi_note,
                "note": _note_name(midi_note),
                "duration_beats": durations[(index + value) % len(durations)],
                "velocity": min(127, max(52, int(64 + (probability * 48)))),
                "probability": round(probability, 4),
            }
        )

    return events


def _rotate(values: list[int], count: int) -> list[int]:
    if not values:
        return values
    offset = count % len(values)
    return values[offset:] + [value + 12 for value in values[:offset]]


def _counts_to_progression(counts: dict[str, int], root: str, mode: str, bars: int, octave: int) -> list[dict]:
    scale = SCALE_INTERVALS[mode]
    triad = TRIAD_BY_MODE[mode]
    sequence = _expanded_measurements(counts, bars)
    total = sum(counts.values()) or 1
    progression = []

    for bar, bitstring in enumerate(sequence):
        value = int(bitstring, 2)
        degree = value % len(scale)
        chord_root = _midi_number(root, octave, scale[degree])
        base_notes = [chord_root + interval for interval in triad]
        if value & 0b100:
            base_notes.append(chord_root + 10)
        voiced = _rotate(base_notes, (value // len(scale)) % len(base_notes))
        probability = counts.get(bitstring, 0) / total
        progression.append(
            {
                "bar": bar + 1,
                "bitstring": bitstring,
                "degree": degree + 1,
                "notes": [_note_name(note) for note in voiced],
                "midi": voiced,
                "duration_beats": 4,
                "probability": round(probability, 4),
            }
        )

    return progression


def _build_composition_summary(melody: list[dict], progression: list[dict], root: str, mode: str, bpm: int) -> dict:
    return {
        "key": f"{root} {mode}",
        "tempo": bpm,
        "melody_steps": len(melody),
        "bars": len(progression),
        "note_span": [melody[0]["note"], melody[-1]["note"]] if melody else [],
        "first_chord": progression[0]["notes"] if progression else [],
    }


def _build_seed_circuit(qubit_count: int, entanglement: float, phase_bias: float):
    qiskit = _load_qiskit()
    if not qiskit.get("available"):
        raise RuntimeError(qiskit.get("error") or "Qiskit is not installed")

    QuantumCircuit = qiskit["QuantumCircuit"]
    circuit = QuantumCircuit(qubit_count, qubit_count)

    for qubit in range(qubit_count):
        circuit.h(qubit)
        circuit.ry(phase_bias + (qubit * 0.173), qubit)

    entangling_edges = max(0, min(qubit_count - 1, round(entanglement * (qubit_count - 1))))
    for qubit in range(entangling_edges):
        circuit.cx(qubit, qubit + 1)

    for qubit in range(qubit_count):
        circuit.rz((qubit + 1) * phase_bias * 0.5, qubit)

    circuit.measure(range(qubit_count), range(qubit_count))
    return circuit


def _resolve_runner(preferred_backend: str) -> tuple[str, str, callable]:
    qiskit = _load_qiskit()
    if not qiskit.get("available"):
        raise RuntimeError(qiskit.get("error") or "Qiskit is not installed")

    backend_name = preferred_backend.strip().lower()
    if backend_name not in {"auto", "aer", "ibm"}:
        raise ValueError("backend must be one of: auto, aer, ibm")

    if backend_name in {"auto", "ibm"} and _has_ibm_credentials() and qiskit.get("QiskitRuntimeService"):
        service_class = qiskit["QiskitRuntimeService"]
        service = service_class(
            channel=os.environ.get("IBM_QUANTUM_CHANNEL", "ibm_quantum"),
            token=os.environ.get("IBM_QUANTUM_TOKEN"),
            instance=os.environ.get("IBM_QUANTUM_INSTANCE") or None,
        )
        configured_backend = os.environ.get("IBM_QUANTUM_BACKEND")
        if configured_backend and configured_backend != "auto":
            backend = service.backend(configured_backend)
        else:
            backend = service.least_busy(operational=True, simulator=False)

        def run_ibm(circuit, shots: int) -> dict:
            compiled = qiskit["transpile"](circuit, backend)
            result = backend.run(compiled, shots=shots).result()
            return {
                "counts": result.get_counts(),
                "backend": getattr(backend, "name", "ibm_quantum"),
                "backend_kind": "ibm_quantum",
            }

        return getattr(backend, "name", "ibm_quantum"), "ibm_quantum", run_ibm

    if not qiskit.get("AerSimulator"):
        raise RuntimeError(qiskit.get("aer_error") or "Aer simulator is not installed")

    simulator = qiskit["AerSimulator"]()

    def run_aer(circuit, shots: int) -> dict:
        compiled = qiskit["transpile"](circuit, simulator)
        result = simulator.run(compiled, shots=shots).result()
        return {
            "counts": result.get_counts(),
            "backend": "aer_simulator",
            "backend_kind": "simulator",
        }

    return "aer_simulator", "simulator", run_aer


def _sample_counts(circuit, shots: int, backend: str) -> dict:
    resolved_backend, backend_kind, runner = _resolve_runner(backend)
    sampled = runner(circuit, shots)
    sampled["backend"] = sampled.get("backend", resolved_backend)
    sampled["backend_kind"] = sampled.get("backend_kind", backend_kind)
    sampled["shots"] = shots
    return sampled


@mcp.tool()
def quantum_music_status() -> str:
    """Report IBM Quantum and simulator availability for the music server."""
    return json.dumps(_status_payload(), indent=2)


@mcp.tool()
def compose_quantum_melody(
    root: str = "C",
    mode: str = "minor",
    steps: int = 16,
    shots: int = 512,
    octave: int = 4,
    entanglement: float = 0.75,
    phase_bias: float = 0.6,
    backend: str = "auto",
) -> str:
    """Compose a melody by sampling a quantum circuit on IBM Quantum or Aer.

    Args:
        root: Root note, for example C, D#, or Bb.
        mode: Scale or mode name.
        steps: Number of melodic events to generate.
        shots: Number of measurement shots.
        octave: Base octave for the phrase.
        entanglement: 0.0-1.0 amount of entanglement between adjacent qubits.
        phase_bias: Rotation bias used to shape note probabilities.
        backend: auto, aer, or ibm.
    """
    normalized_root = _normalize_root(root)
    normalized_mode = _normalize_mode(mode)
    qubit_count = max(2, min(6, math.ceil(math.log2(max(steps, 2)))))

    try:
        circuit = _build_seed_circuit(qubit_count, entanglement, phase_bias)
        sampled = _sample_counts(circuit, shots, backend)
    except Exception as exc:
        return json.dumps({"error": str(exc), "status": _status_payload()})

    melody = _counts_to_melody(sampled["counts"], normalized_root, normalized_mode, steps, octave)
    return json.dumps(
        {
            "backend": sampled["backend"],
            "backend_kind": sampled["backend_kind"],
            "counts": sampled["counts"],
            "melody": melody,
            "summary": {
                "key": f"{normalized_root} {normalized_mode}",
                "steps": steps,
                "shots": shots,
                "qubits": qubit_count,
            },
        },
        indent=2,
    )


@mcp.tool()
def compose_quantum_progression(
    root: str = "C",
    mode: str = "minor",
    bars: int = 8,
    shots: int = 512,
    octave: int = 3,
    entanglement: float = 0.8,
    phase_bias: float = 0.45,
    backend: str = "auto",
) -> str:
    """Compose a chord progression from entangled qubit measurements."""
    normalized_root = _normalize_root(root)
    normalized_mode = _normalize_mode(mode)
    qubit_count = max(2, min(5, math.ceil(math.log2(max(bars, 2))) + 1))

    try:
        circuit = _build_seed_circuit(qubit_count, entanglement, phase_bias)
        sampled = _sample_counts(circuit, shots, backend)
    except Exception as exc:
        return json.dumps({"error": str(exc), "status": _status_payload()})

    progression = _counts_to_progression(sampled["counts"], normalized_root, normalized_mode, bars, octave)
    return json.dumps(
        {
            "backend": sampled["backend"],
            "backend_kind": sampled["backend_kind"],
            "counts": sampled["counts"],
            "progression": progression,
            "summary": {
                "key": f"{normalized_root} {normalized_mode}",
                "bars": bars,
                "shots": shots,
                "qubits": qubit_count,
            },
        },
        indent=2,
    )


@mcp.tool()
def sonify_quantum_circuit(
    circuit_qasm: str,
    root: str = "C",
    mode: str = "minor",
    steps: int = 16,
    bars: int = 4,
    shots: int = 512,
    melody_octave: int = 4,
    harmony_octave: int = 3,
    tempo: int = 96,
    backend: str = "auto",
) -> str:
    """Turn an OpenQASM circuit into a melody and chord progression."""
    normalized_root = _normalize_root(root)
    normalized_mode = _normalize_mode(mode)
    qiskit = _load_qiskit()
    if not qiskit.get("available"):
        return json.dumps({"error": qiskit.get("error"), "status": _status_payload()})

    try:
        circuit = qiskit["QuantumCircuit"].from_qasm_str(circuit_qasm)
        if circuit.num_clbits == 0:
            circuit.measure_all()
        sampled = _sample_counts(circuit, shots, backend)
    except Exception as exc:
        return json.dumps({"error": str(exc), "status": _status_payload()})

    melody = _counts_to_melody(sampled["counts"], normalized_root, normalized_mode, steps, melody_octave)
    progression = _counts_to_progression(sampled["counts"], normalized_root, normalized_mode, bars, harmony_octave)
    return json.dumps(
        {
            "backend": sampled["backend"],
            "backend_kind": sampled["backend_kind"],
            "counts": sampled["counts"],
            "melody": melody,
            "progression": progression,
            "summary": _build_composition_summary(melody, progression, normalized_root, normalized_mode, tempo),
        },
        indent=2,
    )


def main():
    """Entry point for the IBM Quantum music MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()