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


def git_ops(action: str, repo_path: str, args: str = "") -> str:
    """
    Run a structured git operation against a local repository.

    Umbrella tool surfaced to agents that declare `git_ops` as a single
    tool name rather than each subcommand individually (the Crowe Talon
    agent in particular). Dispatches to the more specific helpers in
    this module for known actions, and falls back to a raw forward of
    the rest of the git CLI for actions in the allowlist.

    :param action: Git subcommand. Allowlisted; unknown actions
        are rejected before any shell call.
    :param repo_path: Absolute path to the git repository to operate on.
    :param args: Trailing arguments forwarded to git. Empty by default.
        For `commit`, supply the message as a single quoted string.
        For `add`, supply the path spec.
    :return: JSON string with `output`/`stdout`, `stderr`, `return_code`.
    :rtype: str
    """
    action = (action or "").strip().lower()
    allowed = {
        # Read-only
        "status", "diff", "log", "show", "branch", "remote", "config",
        "rev-parse", "describe", "ls-files", "blame", "shortlog",
        # Mutating — agent prompt is expected to surface the command first
        "add", "commit", "fetch", "pull", "push", "checkout", "switch",
        "merge", "rebase", "tag", "stash", "restore", "reset", "rm", "mv",
        "init",
    }
    if action not in allowed:
        return json.dumps({
            "error": f"git action {action!r} is not allowed",
            "allowed": sorted(allowed),
            "return_code": -1,
        })

    # Route through the dedicated helpers when available so their
    # canonical flag choices (e.g. status --porcelain=v2) stay in
    # one place.
    if action == "status":
        return git_status(repo_path)
    if action == "diff":
        return git_diff(repo_path, staged=args.strip() == "--staged" or "--cached" in args)
    if action == "log":
        try:
            count = int(args) if args.strip().isdigit() else 10
        except ValueError:
            count = 10
        return git_log(repo_path, count=count)

    # Generic path for the rest of the allowlist.
    import shlex as _shlex
    try:
        tail = _shlex.split(args) if args else []
    except ValueError as exc:
        return json.dumps({"error": f"could not parse args: {exc}", "return_code": -1})
    return _git(repo_path, [action, *tail])


def _git(repo_path: str, args: list) -> str:
    """Run a git command in the given repo.

    :param repo_path: Absolute path to the git repository.
    :param args: Trailing argv passed straight to the git CLI.
    :return: JSON with output / stderr / return_code, or an error envelope.
    :rtype: str
    """
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
