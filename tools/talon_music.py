"""
Talon Music Engine tool — quantum-powered composition via @talon/* packages.
Interfaces with the Talon CLI and core libraries at ~/Projects/talon/.
"""

import json
import subprocess
import os

TALON_PATH = "/Users/crowelogic/Projects/talon"


def talon_compose(style: str = "ambient", duration_bars: int = 16, quantum_mode: str = "superposition") -> str:
    """
    Generate a musical composition using the Talon engine.
    Leverages quantum-enhanced algorithms for creative variation.

    :param style: Musical style (ambient, cinematic, electronic, jazz, experimental).
    :param duration_bars: Length in bars (default 16).
    :param quantum_mode: Quantum generation mode (superposition, entanglement, interference).
    :return: JSON with composition data (MIDI events, structure, quantum metrics).
    :rtype: str
    """
    return _run_talon_cli(["compose", "--style", style, "--bars", str(duration_bars), "--quantum", quantum_mode])


def talon_import_midi(midi_path: str) -> str:
    """
    Import a MIDI file into Talon for analysis and transformation.

    :param midi_path: Absolute path to the MIDI file.
    :return: JSON with imported track data (notes, tempo, structure).
    :rtype: str
    """
    return _run_talon_cli(["import", midi_path])


def talon_analyze(input_source: str) -> str:
    """
    Analyze a MIDI file or Talon composition for musical properties.

    :param input_source: Path to MIDI file or Talon composition ID.
    :return: JSON with analysis (key, tempo, harmony, rhythm, complexity).
    :rtype: str
    """
    return _run_talon_cli(["analyze", input_source])


def talon_transform(input_source: str, transformation: str) -> str:
    """
    Apply a quantum transformation to a composition or MIDI file.

    :param input_source: Path to MIDI file or Talon composition ID.
    :param transformation: Transformation to apply (transpose, invert, retrograde, quantum-evolve, fractal-expand).
    :return: JSON with transformed composition data.
    :rtype: str
    """
    return _run_talon_cli(["transform", input_source, "--type", transformation])


def talon_export(composition_id: str, format: str = "midi", output_path: str = "") -> str:
    """
    Export a Talon composition to a file.

    :param composition_id: The composition ID to export.
    :param format: Export format (midi, wav, json, ableton-als).
    :param output_path: Optional output path (default: ~/Desktop/).
    :return: JSON with export result and file path.
    :rtype: str
    """
    cmd = ["export", composition_id, "--format", format]
    if output_path:
        cmd.extend(["--output", output_path])
    return _run_talon_cli(cmd)


def _run_talon_cli(args: list) -> str:
    """Run a Talon CLI command."""
    if not os.path.isdir(TALON_PATH):
        return json.dumps({"error": f"Talon project not found at {TALON_PATH}"})

    try:
        # Try the CLI first, fall back to npx/node
        result = subprocess.run(
            ["npx", "talon"] + args,
            capture_output=True, text=True, timeout=60,
            cwd=TALON_PATH,
            env={**os.environ, "NODE_PATH": os.path.join(TALON_PATH, "node_modules")},
        )

        if result.returncode != 0 and "not found" in result.stderr.lower():
            # Fall back to direct node execution
            cli_path = os.path.join(TALON_PATH, "packages", "cli", "src", "index.ts")
            result = subprocess.run(
                ["npx", "tsx", cli_path] + args,
                capture_output=True, text=True, timeout=60,
                cwd=TALON_PATH,
            )

        output = result.stdout.strip()
        if len(output) > 50000:
            output = output[:50000] + "\n... (truncated)"

        return json.dumps({
            "output": output,
            "stderr": result.stderr.strip() if result.stderr else "",
            "return_code": result.returncode,
        })
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "Talon command timed out after 60s"})
    except Exception as e:
        return json.dumps({"error": str(e)})
