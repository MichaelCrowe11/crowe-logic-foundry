"""
Talon Music Engine tool — quantum-powered composition via @talon/* packages.
Interfaces with the Talon CLI and core libraries at ~/Projects/talon/.
"""

import json
import subprocess
import os

TALON_PATH = "/Users/crowelogic/Projects/talon"
DEFAULT_OUTPUT = os.path.expanduser("~/Desktop")


def talon_generate_chords(root: str = "A", scale: str = "minor", bars: int = 8,
                          tempo: int = 85, groove: str = "swing",
                          style: str = "", output: str = "") -> str:
    """
    Generate a chord progression as MIDI.

    :param root: Root note (A, C, D, F#, Bb, etc.).
    :param scale: Scale (major, minor, pentatonic, dorian, mixolydian, blues).
    :param bars: Number of bars (default 8).
    :param tempo: BPM (default 85).
    :param groove: Groove profile (tight, loose, swing, funk, prog, floyd, devastating).
    :param style: Progression style (sad, happy, dark, chill) — optional.
    :param output: Output file path (default: ~/Desktop/chords.mid).
    :return: JSON with generation result.
    :rtype: str
    """
    out = output or os.path.join(DEFAULT_OUTPUT, f"chords-{root}{scale}-{tempo}bpm.mid")
    cmd = ["midi", "chords", "--root", root, "--scale", scale,
           "--bars", str(bars), "-t", str(tempo), "-g", groove, "--output", out]
    if style:
        cmd.extend(["--style", style])
    return _run_talon_cli(cmd, out)


def talon_generate_drums(genre: str = "breakbeat", bars: int = 8,
                         tempo: int = 85, groove: str = "swing",
                         output: str = "") -> str:
    """
    Generate a drum pattern as MIDI.

    :param genre: Drum genre (trap, boom-bap, four-on-the-floor, breakbeat, lofi).
    :param bars: Number of bars (default 8).
    :param tempo: BPM (default 85).
    :param groove: Groove profile (drummer_human, funk, swing, devastating).
    :param output: Output file path (default: ~/Desktop/drums.mid).
    :return: JSON with generation result.
    :rtype: str
    """
    out = output or os.path.join(DEFAULT_OUTPUT, f"drums-{genre}-{tempo}bpm.mid")
    cmd = ["midi", "drums", "--genre", genre, "--bars", str(bars),
           "-t", str(tempo), "-g", groove, "--output", out]
    return _run_talon_cli(cmd, out)


def talon_generate_melody(root: str = "A", scale: str = "minor", bars: int = 8,
                          tempo: int = 85, density: float = 0.5,
                          groove: str = "floyd", output: str = "") -> str:
    """
    Generate a melody line as MIDI.

    :param root: Root note (A, C, D, etc.).
    :param scale: Scale (pentatonic, minor, major, blues, dorian).
    :param bars: Number of bars (default 8).
    :param tempo: BPM (default 85).
    :param density: Note density 0.0-1.0 (default 0.5).
    :param groove: Groove profile (floyd, loose, prog, devastating).
    :param output: Output file path (default: ~/Desktop/melody.mid).
    :return: JSON with generation result.
    :rtype: str
    """
    out = output or os.path.join(DEFAULT_OUTPUT, f"melody-{root}{scale}-{tempo}bpm.mid")
    cmd = ["midi", "melody", "--root", root, "--scale", scale,
           "--bars", str(bars), "-t", str(tempo), "--density", str(density),
           "-g", groove, "--output", out]
    return _run_talon_cli(cmd, out)


def talon_quantum_melody(key: str = "Am", style: str = "miles",
                         notes: int = 16) -> str:
    """
    Generate a melody using quantum probability amplitudes.
    Uses interference patterns for musically coherent, non-deterministic phrases.

    :param key: Musical key (Am, C, Em, Dm, F#m, etc.).
    :param style: Tension style (miles, coltrane, ambient).
    :param notes: Number of notes to generate (default 16).
    :return: JSON with quantum melody data (MIDI note numbers + names).
    :rtype: str
    """
    cmd = ["quantum", "melody", "-k", key, "-s", style, "-n", str(notes)]
    return _run_talon_cli(cmd)


def talon_quantum_chord(key: str = "Am", tension: float = 0.5) -> str:
    """
    Generate a chord voicing from quantum superposition.
    Higher tension = more dissonance and extended voicings.

    :param key: Musical key (Am, C, Em, etc.).
    :param tension: Tension level 0.0-1.0 (default 0.5).
    :return: JSON with quantum chord voicing.
    :rtype: str
    """
    cmd = ["quantum", "chord", "-k", key, "-t", str(tension)]
    return _run_talon_cli(cmd)


def talon_compose_emotion(emotion: str = "nostalgia", key: str = "Am",
                          bars: int = 16, tempo: int = 0,
                          output: str = "") -> str:
    """
    Compose a full multi-track piece from an emotion preset.
    Generates chords, melody, and drums tracks as MIDI.

    Available emotions: grief, rage, bliss, anxiety, nostalgia, awe, longing,
    triumph, dread, serenity, wonder, melancholy, fury, tenderness, desolation,
    ecstasy, suspense, ethereal.

    :param emotion: Emotion preset name (default: nostalgia).
    :param key: Musical key (Am, C, Em, etc.).
    :param bars: Number of bars (default 16).
    :param tempo: Override tempo (0 = auto from emotion).
    :param output: Output file path (default: ~/Desktop/<emotion>.mid).
    :return: JSON with composition result.
    :rtype: str
    """
    out = output or os.path.join(DEFAULT_OUTPUT, f"{emotion}-{key}-{bars}bars.mid")
    cmd = ["emotion", emotion, "-k", key, "-b", str(bars), "-o", out]
    if tempo > 0:
        cmd.extend(["-t", str(tempo)])
    return _run_talon_cli(cmd, out)


def talon_full_composition(title: str = "composition", root: str = "A",
                           scale: str = "minor", bars: int = 32,
                           tempo: int = 85, sections: str = "intro,theme,solo,bridge,outro",
                           groove: str = "swing", drum_genre: str = "breakbeat",
                           melody_density: float = 0.5, output_dir: str = "") -> str:
    """
    Generate a complete multi-section, multi-track composition as separate MIDI files.
    Creates chord, melody, bass, and drum tracks for each section.
    Perfect for importing into Ableton Live or any DAW as stems.

    :param title: Composition title (used in filenames).
    :param root: Root note (A, C, D, etc.).
    :param scale: Scale (minor, major, dorian, mixolydian, blues, pentatonic).
    :param bars: Total bars across all sections (default 32).
    :param tempo: BPM (default 85).
    :param sections: Comma-separated section names (default: intro,theme,solo,bridge,outro).
    :param groove: Main groove profile (swing, funk, prog, floyd, devastating).
    :param drum_genre: Drum pattern genre (breakbeat, boom-bap, lofi, trap).
    :param melody_density: Note density 0.0-1.0 (default 0.5).
    :param output_dir: Output directory (default: ~/Desktop/<title>/).
    :return: JSON with all generated files and structure.
    :rtype: str
    """
    out_dir = output_dir or os.path.join(DEFAULT_OUTPUT, title)
    os.makedirs(out_dir, exist_ok=True)

    section_list = [s.strip() for s in sections.split(",")]
    bars_per_section = max(4, bars // len(section_list))
    results = {"title": title, "tempo": tempo, "key": f"{root} {scale}",
               "sections": section_list, "files": []}

    # Groove mapping per section type for variety
    section_grooves = {
        "intro": "floyd", "theme": groove, "solo": "devastating",
        "bridge": "prog", "outro": "floyd", "verse": groove,
        "chorus": "funk", "breakdown": "loose",
    }
    # Density mapping per section
    section_density = {
        "intro": 0.3, "theme": melody_density, "solo": 0.8,
        "bridge": 0.4, "outro": 0.3, "verse": melody_density,
        "chorus": 0.6, "breakdown": 0.2,
    }

    for section in section_list:
        sec_groove = section_grooves.get(section, groove)
        sec_density = section_density.get(section, melody_density)
        prefix = os.path.join(out_dir, f"{section}")

        # Chords
        chord_out = f"{prefix}-chords.mid"
        _run_talon_cli(["midi", "chords", "--root", root, "--scale", scale,
                        "--bars", str(bars_per_section), "-t", str(tempo),
                        "-g", sec_groove, "--output", chord_out])
        results["files"].append(chord_out)

        # Melody
        melody_out = f"{prefix}-melody.mid"
        _run_talon_cli(["midi", "melody", "--root", root, "--scale", scale,
                        "--bars", str(bars_per_section), "-t", str(tempo),
                        "--density", str(sec_density), "-g", sec_groove,
                        "--output", melody_out])
        results["files"].append(melody_out)

        # Bass (low-octave melody with funk groove)
        bass_out = f"{prefix}-bass.mid"
        _run_talon_cli(["midi", "melody", "--root", root, "--scale", scale,
                        "--bars", str(bars_per_section), "-t", str(tempo),
                        "--density", str(max(0.3, sec_density - 0.2)),
                        "-g", "funk", "--output", bass_out])
        results["files"].append(bass_out)

        # Drums
        drums_out = f"{prefix}-drums.mid"
        _run_talon_cli(["midi", "drums", "--genre", drum_genre,
                        "--bars", str(bars_per_section), "-t", str(tempo),
                        "-g", "drummer_human", "--output", drums_out])
        results["files"].append(drums_out)

    return json.dumps(results, indent=2)


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
    Analyze a MIDI file or audio for musical properties.

    :param input_source: Path to MIDI file or audio file.
    :return: JSON with analysis (key, tempo, harmony, rhythm, complexity).
    :rtype: str
    """
    return _run_talon_cli(["analyze", input_source])


def talon_list_grooves() -> str:
    """
    List all available groove profiles with their characteristics.
    Grooves control timing jitter, swing, ghost notes, and tempo drift.

    :return: Text listing of all groove profiles.
    :rtype: str
    """
    return _run_talon_cli(["quantum", "humanize", "--list-grooves"])


def talon_list_emotions() -> str:
    """
    List all available emotion presets with their parameters.
    Emotions control valence, energy, tension, warmth, and density.

    :return: Text listing of all emotion presets.
    :rtype: str
    """
    return _run_talon_cli(["emotion", "--list"])


def _run_talon_cli(args: list, expected_output: str = "") -> str:
    """Run a Talon CLI command."""
    if not os.path.isdir(TALON_PATH):
        return json.dumps({"error": f"Talon project not found at {TALON_PATH}"})

    try:
        result = subprocess.run(
            ["npx", "talon"] + args,
            capture_output=True, text=True, timeout=120,
            cwd=TALON_PATH,
            env={**os.environ, "NODE_PATH": os.path.join(TALON_PATH, "node_modules")},
        )

        if result.returncode != 0 and "not found" in result.stderr.lower():
            cli_path = os.path.join(TALON_PATH, "packages", "cli", "src", "index.ts")
            result = subprocess.run(
                ["npx", "tsx", cli_path] + args,
                capture_output=True, text=True, timeout=120,
                cwd=TALON_PATH,
            )

        output = result.stdout.strip()
        if len(output) > 50000:
            output = output[:50000] + "\n... (truncated)"

        response = {
            "output": output,
            "return_code": result.returncode,
        }
        if result.stderr.strip():
            response["stderr"] = result.stderr.strip()
        if expected_output and os.path.exists(expected_output):
            response["file"] = expected_output
            response["file_size"] = os.path.getsize(expected_output)

        return json.dumps(response)
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "Talon command timed out after 120s"})
    except Exception as e:
        return json.dumps({"error": str(e)})
