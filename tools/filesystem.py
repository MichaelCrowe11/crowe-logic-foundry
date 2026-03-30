"""
Filesystem tools — read, write, edit, and list files.
"""

import os
import json
from pathlib import Path


def read_file(file_path: str, offset: int = 0, limit: int = 0) -> str:
    """
    Read the contents of a file. Returns the file content as a string.

    :param file_path: Absolute path to the file to read.
    :param offset: Line number to start reading from (0-based, default 0).
    :param limit: Maximum number of lines to return (0 = unlimited).
    :return: File contents with line numbers prefixed.
    :rtype: str
    """
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        return json.dumps({"error": f"File not found: {file_path}"})
    if not path.is_file():
        return json.dumps({"error": f"Not a file: {file_path}"})
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        if offset > 0:
            lines = lines[offset:]
        if limit > 0:
            lines = lines[:limit]
        numbered = [f"{i + offset + 1:>6}\t{line}" for i, line in enumerate(lines)]
        return "\n".join(numbered)
    except Exception as e:
        return json.dumps({"error": str(e)})


def write_file(file_path: str, content: str) -> str:
    """
    Write content to a file, creating it if it doesn't exist.
    Overwrites existing content.

    :param file_path: Absolute path to the file to write.
    :param content: The full content to write to the file.
    :return: Confirmation message with bytes written.
    :rtype: str
    """
    path = Path(file_path).expanduser().resolve()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return json.dumps({"success": True, "path": str(path), "bytes": len(content.encode("utf-8"))})
    except Exception as e:
        return json.dumps({"error": str(e)})


def edit_file(file_path: str, old_string: str, new_string: str) -> str:
    """
    Replace an exact string in a file. The old_string must appear exactly once
    in the file for the replacement to succeed.

    :param file_path: Absolute path to the file to edit.
    :param old_string: The exact text to find and replace.
    :param new_string: The replacement text.
    :return: Confirmation or error message.
    :rtype: str
    """
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        return json.dumps({"error": f"File not found: {file_path}"})
    try:
        text = path.read_text(encoding="utf-8")
        count = text.count(old_string)
        if count == 0:
            return json.dumps({"error": "old_string not found in file"})
        if count > 1:
            return json.dumps({"error": f"old_string found {count} times — must be unique. Provide more context."})
        new_text = text.replace(old_string, new_string, 1)
        path.write_text(new_text, encoding="utf-8")
        return json.dumps({"success": True, "path": str(path)})
    except Exception as e:
        return json.dumps({"error": str(e)})


def list_directory(directory_path: str, pattern: str = "*", recursive: bool = False) -> str:
    """
    List files and directories at the given path. Supports glob patterns.

    :param directory_path: Absolute path to the directory to list.
    :param pattern: Glob pattern to filter results (default "*").
    :param recursive: If true, search recursively using "**/" prefix.
    :return: JSON list of file/directory entries with type and size.
    :rtype: str
    """
    path = Path(directory_path).expanduser().resolve()
    if not path.exists():
        return json.dumps({"error": f"Directory not found: {directory_path}"})
    if not path.is_dir():
        return json.dumps({"error": f"Not a directory: {directory_path}"})
    try:
        if recursive:
            entries = sorted(path.rglob(pattern))
        else:
            entries = sorted(path.glob(pattern))

        results = []
        for entry in entries[:500]:  # Cap at 500 to avoid huge outputs
            results.append({
                "name": entry.name,
                "path": str(entry),
                "type": "dir" if entry.is_dir() else "file",
                "size": entry.stat().st_size if entry.is_file() else None,
            })
        return json.dumps({"count": len(results), "entries": results})
    except Exception as e:
        return json.dumps({"error": str(e)})
