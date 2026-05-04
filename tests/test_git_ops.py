"""Tests for tools/git_ops.py — git status, diff, log, commit, clone."""

import json
import os
import subprocess
import pytest


@pytest.fixture()
def git_mod():
    from tools.git_ops import git_status, git_diff, git_log, git_commit, git_clone
    return git_status, git_diff, git_log, git_commit, git_clone


@pytest.fixture()
def git_repo(tmp_path):
    """Create a throwaway git repo with one commit."""
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
    (tmp_path / "repo" / "README.md").write_text("init")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, capture_output=True)
    return repo


class TestGitStatus:
    def test_clean_repo(self, git_mod, git_repo):
        git_status, *_ = git_mod
        result = json.loads(git_status(git_repo))
        assert result.get("return_code", 0) == 0

    def test_shows_untracked_file(self, git_mod, git_repo):
        git_status, *_ = git_mod
        open(os.path.join(git_repo, "new.txt"), "w").write("x")
        result = json.loads(git_status(git_repo))
        assert "new.txt" in result["output"]

    def test_returns_error_for_non_repo(self, git_mod, tmp_path):
        git_status, *_ = git_mod
        result = json.loads(git_status(str(tmp_path)))
        assert "error" in result


class TestGitDiff:
    def test_empty_diff_on_clean_repo(self, git_mod, git_repo):
        _, git_diff, *_ = git_mod
        result = json.loads(git_diff(git_repo))
        assert result.get("return_code", 0) == 0

    def test_shows_unstaged_changes(self, git_mod, git_repo):
        _, git_diff, *_ = git_mod
        with open(os.path.join(git_repo, "README.md"), "w") as f:
            f.write("changed")
        result = json.loads(git_diff(git_repo))
        assert "README" in result.get("output", "")


class TestGitLog:
    def test_shows_initial_commit(self, git_mod, git_repo):
        *_, git_log, _, _ = git_mod
        result = json.loads(git_log(git_repo, count=5))
        assert "initial" in result["output"]

    def test_count_capped_at_50(self, git_mod, git_repo):
        *_, git_log, _, _ = git_mod
        # Should not error even with absurd count
        result = json.loads(git_log(git_repo, count=9999))
        assert result.get("return_code", 0) == 0


class TestGitCommit:
    def test_commits_new_file(self, git_mod, git_repo):
        *_, git_commit, _ = git_mod
        open(os.path.join(git_repo, "added.txt"), "w").write("data")
        result = json.loads(git_commit(git_repo, "add file", "added.txt"))
        assert result.get("return_code", 0) == 0
        assert "add file" in result.get("output", "")

    def test_errors_on_non_repo(self, git_mod, tmp_path):
        *_, git_commit, _ = git_mod
        result = json.loads(git_commit(str(tmp_path), "msg"))
        assert "error" in result


class TestGitClone:
    def test_clone_invalid_url_returns_error(self, git_mod, tmp_path):
        *_, git_clone = git_mod
        target = str(tmp_path / "clone-target")
        result = json.loads(git_clone("https://invalid.example.com/no-repo.git", target))
        assert result["return_code"] != 0
