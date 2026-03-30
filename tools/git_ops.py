"""
Git operations tool — repo management, commits, branches, diffs.
"""

import json
import subprocess
import os


def git_status(repo_path: str) -> str:
    """
    Show the working tree status of a git repository.

    :param repo_path: Absolute path to the git repository.
    :return: JSON with status output (staged, unstaged, untracked files).
    :rtype: str
    """
    return _git(repo_path, ["status", "--porcelain=v2", "--branch"])


def git_diff(repo_path: str, staged: bool = False) -> str:
    """
    Show changes in the working directory or staging area.

    :param repo_path: Absolute path to the git repository.
    :param staged: If true, show staged changes. Default shows unstaged.
    :return: JSON with diff output.
    :rtype: str
    """
    cmd = ["diff", "--stat"]
    if staged:
        cmd.append("--cached")
    return _git(repo_path, cmd)


def git_log(repo_path: str, count: int = 10) -> str:
    """
    Show recent commit history.

    :param repo_path: Absolute path to the git repository.
    :param count: Number of commits to show (default 10).
    :return: JSON with commit history (hash, author, date, message).
    :rtype: str
    """
    count = min(count, 50)
    return _git(repo_path, ["log", f"-{count}", "--oneline", "--decorate"])


def git_commit(repo_path: str, message: str, files: str = ".") -> str:
    """
    Stage files and create a commit.

    :param repo_path: Absolute path to the git repository.
    :param message: The commit message.
    :param files: Files to stage (default "." for all changes).
    :return: JSON with commit result.
    :rtype: str
    """
    add_result = _git(repo_path, ["add", files])
    if "error" in add_result:
        return add_result
    return _git(repo_path, ["commit", "-m", message])


def git_clone(url: str, target_path: str) -> str:
    """
    Clone a git repository.

    :param url: The repository URL to clone.
    :param target_path: Local path to clone into.
    :return: JSON with clone result.
    :rtype: str
    """
    try:
        result = subprocess.run(
            ["git", "clone", url, target_path],
            capture_output=True, text=True, timeout=120,
        )
        return json.dumps({
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "return_code": result.returncode,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def _git(repo_path: str, args: list) -> str:
    """Run a git command in the given repo."""
    repo = os.path.expanduser(repo_path)
    if not os.path.isdir(os.path.join(repo, ".git")):
        return json.dumps({"error": f"Not a git repository: {repo_path}"})
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True, text=True, timeout=30, cwd=repo,
        )
        output = result.stdout.strip()
        if len(output) > 50000:
            output = output[:50000] + "\n... (truncated)"
        return json.dumps({
            "output": output,
            "stderr": result.stderr.strip() if result.stderr else "",
            "return_code": result.returncode,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})
