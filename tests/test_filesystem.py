"""Tests for tools/filesystem.py — read, write, edit, list."""

import json
import os
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def fs_mod():
    """Import the filesystem module from the project root."""
    from tools.filesystem import read_file, write_file, edit_file, list_directory
    return read_file, write_file, edit_file, list_directory


@pytest.fixture()
def tmp_text(tmp_path):
    """Create a small text file and return its path."""
    p = tmp_path / "sample.txt"
    p.write_text("line-0\nline-1\nline-2\nline-3\nline-4\n")
    return str(p)


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


class TestReadFile:
    def test_reads_existing_file(self, fs_mod, tmp_text):
        read_file, *_ = fs_mod
        result = read_file(tmp_text)
        assert "line-0" in result
        assert "line-4" in result

    def test_returns_error_for_missing_file(self, fs_mod):
        read_file, *_ = fs_mod
        result = json.loads(read_file("/nonexistent/path.txt"))
        assert "error" in result

    def test_offset_skips_lines(self, fs_mod, tmp_text):
        read_file, *_ = fs_mod
        result = read_file(tmp_text, offset=3)
        assert "line-0" not in result
        assert "line-3" in result

    def test_limit_caps_output(self, fs_mod, tmp_text):
        read_file, *_ = fs_mod
        result = read_file(tmp_text, limit=2)
        lines = [l for l in result.splitlines() if l.strip()]
        assert len(lines) == 2

    def test_returns_error_for_directory(self, fs_mod, tmp_path):
        read_file, *_ = fs_mod
        result = json.loads(read_file(str(tmp_path)))
        assert "error" in result


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


class TestWriteFile:
    def test_creates_new_file(self, fs_mod, tmp_path):
        _, write_file, *_ = fs_mod
        target = str(tmp_path / "new.txt")
        result = json.loads(write_file(target, "hello world"))
        assert result["success"] is True
        assert os.path.exists(target)
        assert open(target).read() == "hello world"

    def test_creates_parent_directories(self, fs_mod, tmp_path):
        _, write_file, *_ = fs_mod
        target = str(tmp_path / "a" / "b" / "c.txt")
        result = json.loads(write_file(target, "deep"))
        assert result["success"] is True
        assert open(target).read() == "deep"

    def test_overwrites_existing_content(self, fs_mod, tmp_text):
        _, write_file, *_ = fs_mod
        result = json.loads(write_file(tmp_text, "replaced"))
        assert result["success"] is True
        assert open(tmp_text).read() == "replaced"

    def test_reports_byte_count(self, fs_mod, tmp_path):
        _, write_file, *_ = fs_mod
        target = str(tmp_path / "bytes.txt")
        content = "abc\u00e9"  # 5 bytes in UTF-8
        result = json.loads(write_file(target, content))
        assert result["bytes"] == len(content.encode("utf-8"))


# ---------------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------------


class TestEditFile:
    def test_replaces_unique_string(self, fs_mod, tmp_text):
        *_, edit_file, _ = fs_mod
        result = json.loads(edit_file(tmp_text, "line-2", "LINE-TWO"))
        assert result["success"] is True
        assert "LINE-TWO" in open(tmp_text).read()

    def test_errors_when_string_not_found(self, fs_mod, tmp_text):
        *_, edit_file, _ = fs_mod
        result = json.loads(edit_file(tmp_text, "nonexistent", "x"))
        assert "error" in result
        assert "not found" in result["error"]

    def test_errors_on_ambiguous_match(self, fs_mod, tmp_path):
        *_, edit_file, _ = fs_mod
        p = tmp_path / "dup.txt"
        p.write_text("aaa\naaa\n")
        result = json.loads(edit_file(str(p), "aaa", "bbb"))
        assert "error" in result
        assert "2 times" in result["error"]

    def test_errors_for_missing_file(self, fs_mod):
        *_, edit_file, _ = fs_mod
        result = json.loads(edit_file("/nonexistent/file.py", "a", "b"))
        assert "error" in result


# ---------------------------------------------------------------------------
# list_directory
# ---------------------------------------------------------------------------


class TestListDirectory:
    def test_lists_directory_contents(self, fs_mod, tmp_path):
        *_, list_directory = fs_mod
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        (tmp_path / "sub").mkdir()
        result = json.loads(list_directory(str(tmp_path)))
        assert result["count"] == 3
        names = {e["name"] for e in result["entries"]}
        assert names == {"a.txt", "b.txt", "sub"}

    def test_glob_pattern_filters(self, fs_mod, tmp_path):
        *_, list_directory = fs_mod
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.txt").write_text("")
        result = json.loads(list_directory(str(tmp_path), pattern="*.py"))
        assert result["count"] == 1
        assert result["entries"][0]["name"] == "a.py"

    def test_recursive_search(self, fs_mod, tmp_path):
        *_, list_directory = fs_mod
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "deep.txt").write_text("")
        (tmp_path / "top.txt").write_text("")
        result = json.loads(list_directory(str(tmp_path), pattern="*.txt", recursive=True))
        assert result["count"] == 2

    def test_returns_error_for_missing_directory(self, fs_mod):
        *_, list_directory = fs_mod
        result = json.loads(list_directory("/nonexistent/dir"))
        assert "error" in result

    def test_returns_error_for_file_path(self, fs_mod, tmp_text):
        *_, list_directory = fs_mod
        result = json.loads(list_directory(tmp_text))
        assert "error" in result

    def test_entries_include_type_and_size(self, fs_mod, tmp_path):
        *_, list_directory = fs_mod
        (tmp_path / "file.txt").write_text("hello")
        (tmp_path / "dir").mkdir()
        result = json.loads(list_directory(str(tmp_path)))
        entries = {e["name"]: e for e in result["entries"]}
        assert entries["file.txt"]["type"] == "file"
        assert entries["file.txt"]["size"] == 5
        assert entries["dir"]["type"] == "dir"
        assert entries["dir"]["size"] is None
