"""
Search tools — web search and local file content search (grep).
"""

import json
import subprocess
import os
import httpx
from bs4 import BeautifulSoup

# Resolve ripgrep path once at import time
_RG_PATH = subprocess.run(
    ["which", "rg"], capture_output=True, text=True
).stdout.strip() or None


def web_search(query: str, num_results: int = 5) -> str:
    """
    Search the web using the system's available search capabilities.
    Returns a list of search results with titles, URLs, and snippets.

    :param query: The search query string.
    :param num_results: Number of results to return (default 5, max 10).
    :return: JSON list of search results.
    :rtype: str
    """
    num_results = min(num_results, 10)

    try:
        response = httpx.get(
            "https://lite.duckduckgo.com/lite/",
            params={"q": query},
            headers={"User-Agent": "CroweLogic/0.1"},
            timeout=15,
            follow_redirects=True,
        )
        soup = BeautifulSoup(response.text, "html.parser")

        results = []
        for link in soup.select("a.result-link, td a[href^='http']"):
            href = link.get("href", "")
            text = link.get_text(strip=True)
            if href.startswith("http") and text and len(results) < num_results:
                results.append({"title": text, "url": href})

        if results:
            return json.dumps({"query": query, "results": results})

        return json.dumps({"query": query, "results": [], "note": "No results found. Try rephrasing."})
    except Exception as e:
        return json.dumps({"error": f"Search failed: {str(e)}"})


def grep_search(pattern: str, path: str = ".", file_glob: str = "", max_results: int = 50) -> str:
    """
    Search file contents using ripgrep (rg) or grep. Supports regex patterns.

    :param pattern: Regex pattern to search for.
    :param path: Directory or file to search in (default: current directory).
    :param file_glob: Optional glob to filter files (e.g. "*.py", "*.ts").
    :param max_results: Maximum number of matching lines to return (default 50).
    :return: JSON with matching lines, file paths, and line numbers.
    :rtype: str
    """
    search_path = os.path.expanduser(path)
    max_results = min(max_results, 200)

    try:
        if _RG_PATH:
            cmd = [_RG_PATH, "--json", "-m", str(max_results), "--no-heading"]
            if file_glob:
                cmd.extend(["--glob", file_glob])
            cmd.extend([pattern, search_path])
        else:
            cmd = ["grep", "-rn", "--include", file_glob or "*", pattern, search_path]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if _RG_PATH:
            # Parse ripgrep JSON output
            matches = []
            for line in result.stdout.splitlines():
                try:
                    entry = json.loads(line)
                    if entry.get("type") == "match":
                        data = entry["data"]
                        matches.append({
                            "file": data["path"]["text"],
                            "line_number": data["line_number"],
                            "text": data["lines"]["text"].rstrip(),
                        })
                except json.JSONDecodeError:
                    continue
            return json.dumps({"pattern": pattern, "count": len(matches), "matches": matches})
        else:
            # Parse grep output
            matches = []
            for line in result.stdout.splitlines()[:max_results]:
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    matches.append({"file": parts[0], "line_number": int(parts[1]), "text": parts[2].rstrip()})
            return json.dumps({"pattern": pattern, "count": len(matches), "matches": matches})

    except subprocess.TimeoutExpired:
        return json.dumps({"error": "Search timed out after 30s"})
    except Exception as e:
        return json.dumps({"error": str(e)})
