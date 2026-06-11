"""Cortex notebook tools — agent-driven Jupyter via the Cortex kernel host.

Six thin HTTP clients over the Crowe Cortex notebook kernel host REST API
(`/v1/notebooks/...`). Notebooks are real `.ipynb` documents the human can
audit live in the Cortex Notebook panel, so the agent's analysis leaves a
persistent, reviewable artifact rather than vanishing into chat scrollback.

Activation:
  * `CORTEX_NOTEBOOK_URL` — base URL of the notebook host (set by Cortex
    only while its notebook host is running). When unset, `register()` is
    a silent no-op so the tools never surface where they can't work.

No network calls happen at import or register time; every tool opens a
short-lived `httpx.Client` per call and always returns a JSON string —
errors included — so the agent loop never sees a raised exception.
"""

from __future__ import annotations

import json
import os
from typing import List, Set

try:
    import httpx
except ImportError:
    httpx = None

ENV_VAR = "CORTEX_NOTEBOOK_URL"
DEFAULT_TIMEOUT = 30.0

_TOOL_NAMES = (
    "notebook_create",
    "notebook_list",
    "notebook_read",
    "notebook_run",
    "notebook_edit_cell",
    "notebook_restart",
)


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------


def _base_url() -> str:
    """Read the notebook-host base URL from the environment at call time."""
    return (os.environ.get(ENV_VAR) or "").rstrip("/")


def _client(timeout: float) -> "httpx.Client":
    """Build an httpx.Client. Module-level factory so tests can patch it."""
    return httpx.Client(timeout=timeout)


def _request(
    method: str, path: str, json_body=None, timeout: float = DEFAULT_TIMEOUT
) -> str:
    """Issue one HTTP request to the notebook host; always return a JSON string."""
    if httpx is None:
        return json.dumps({"error": "httpx not installed in foundry env"})
    try:
        with _client(timeout) as client:
            r = client.request(method, f"{_base_url()}{path}", json=json_body)
        if r.status_code // 100 != 2:
            return json.dumps({"error": r.text, "status_code": r.status_code})
        return r.text
    except Exception as exc:  # noqa: BLE001 - host often not running; degrade to JSON
        return json.dumps(
            {
                "error": f"notebook host unreachable: {exc}",
                "hint": "the Cortex notebook host may not be running",
            }
        )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def notebook_create(name: str) -> str:
    """Create a new notebook in the Cortex Notebook panel.

    The notebook is a persistent .ipynb document the user can watch and audit
    live — prefer one notebook per analysis task, then build it up cell by
    cell with notebook_run. Use markdown cells to narrate between code cells
    so the finished document reads as a report, not a scratchpad.

    :param name: Human-readable notebook name (becomes the .ipynb filename).
    :return: JSON string with the new notebook's ``id``, ``name`` and ``path``,
        or an ``error`` key if the host is unreachable.
    :rtype: str
    """
    return _request("POST", "/v1/notebooks", json_body={"name": name})


def notebook_list() -> str:
    """List the notebooks known to the Cortex notebook host.

    Use this to find an existing notebook's id before reading or appending to
    it, instead of creating a duplicate with notebook_create.

    :return: JSON string with the host's notebook listing (ids, names, paths),
        or an ``error`` key if the host is unreachable.
    :rtype: str
    """
    return _request("GET", "/v1/notebooks")


def notebook_read(notebook_id: str, include_outputs: bool = True) -> str:
    """Read a notebook's cells (and optionally their outputs).

    The full, untruncated outputs always live in the notebook document itself
    — you only need to re-read them here when an earlier notebook_run result
    came back truncated and you actually need more of it. For a cheap
    structural overview (what cells exist, in what order, with what source),
    pass include_outputs=False to keep your context small.

    :param notebook_id: The notebook id (from notebook_create or notebook_list).
    :param include_outputs: When False, strip the outputs from each cell and
        return only structure and source.
    :return: JSON string with the notebook document, or an ``error`` key if
        the host is unreachable.
    :rtype: str
    """
    raw = _request("GET", f"/v1/notebooks/{notebook_id}")
    if include_outputs:
        return raw
    try:
        doc = json.loads(raw)
    except (ValueError, TypeError):
        return raw
    if isinstance(doc, dict):
        cells = doc.get("cells")
        if isinstance(cells, list):
            for cell in cells:
                if isinstance(cell, dict):
                    cell.pop("outputs", None)
        return json.dumps(doc)
    return raw


def notebook_run(
    notebook_id: str, source: str, cell_type: str = "code", timeout: int = 120
) -> str:
    """Append a cell to a notebook and (for code cells) execute it.

    This is the workhorse: each call adds one cell to the persistent .ipynb
    the user is auditing in the Cortex Notebook panel. Kernel variable state
    persists across calls in the same notebook, so build analyses
    incrementally — define data in one cell, plot it in the next. Interleave
    cell_type="markdown" cells to narrate what the code is doing.

    Outputs come back truncated to 8KB plus a mime summary (``mime_summary``
    lists rich outputs like images that can't be inlined); the full output
    lives in the notebook itself — re-read via notebook_read only when you
    genuinely need more than the truncated text. A 409 response means a cell
    is already running in that notebook — wait and retry rather than piling
    on.

    :param notebook_id: The notebook id (from notebook_create or notebook_list).
    :param source: The cell source — Python for code cells, markdown text for
        markdown cells.
    :param cell_type: "code" (default, executed on the kernel) or "markdown"
        (narrative, not executed).
    :param timeout: Seconds the host allows the cell to execute before
        interrupting it.
    :return: JSON string with ``cell_id``, ``status``, ``execution_count``,
        ``output_text``, ``mime_summary`` and ``truncated``, or an ``error``
        key if the host is unreachable.
    :rtype: str
    """
    return _request(
        "POST",
        f"/v1/notebooks/{notebook_id}/cells",
        json_body={"cell_type": cell_type, "source": source, "timeout": timeout},
        timeout=timeout + 30,
    )


def notebook_edit_cell(
    notebook_id: str, cell_id: str, source: str, timeout: int = 120
) -> str:
    """Replace an existing cell's source; code cells are re-executed.

    Use this to fix a cell that errored or to refine a plot in place, instead
    of appending a corrected duplicate — the notebook stays a clean, auditable
    document. Re-running an edited cell does NOT re-run the cells after it; if
    later cells depended on the old value, re-run them too (or restart and
    rebuild). A 409 means a cell is already running in that notebook.

    :param notebook_id: The notebook id (from notebook_create or notebook_list).
    :param cell_id: The cell id (from a prior notebook_run or notebook_read).
    :param source: The new cell source.
    :param timeout: Seconds the host allows the re-execution before
        interrupting it.
    :return: JSON string with ``cell_id``, ``status``, ``execution_count``,
        ``output_text``, ``mime_summary`` and ``truncated``, or an ``error``
        key if the host is unreachable.
    :rtype: str
    """
    return _request(
        "PUT",
        f"/v1/notebooks/{notebook_id}/cells/{cell_id}",
        json_body={"source": source, "timeout": timeout},
        timeout=timeout + 30,
    )


def notebook_restart(notebook_id: str) -> str:
    """Restart a notebook's kernel.

    This wipes kernel variable state (imports, dataframes, everything in
    memory) but NEVER touches the document — all cells and their recorded
    outputs survive. Use it to recover from a hung or corrupted kernel, or to
    verify a notebook runs cleanly top-to-bottom; after restarting, re-run the
    cells whose state you need.

    :param notebook_id: The notebook id (from notebook_create or notebook_list).
    :return: JSON string with the host's restart acknowledgement, or an
        ``error`` key if the host is unreachable.
    :rtype: str
    """
    return _request("POST", f"/v1/notebooks/{notebook_id}/restart")


# ---------------------------------------------------------------------------
# Registration + system-prompt addendum
# ---------------------------------------------------------------------------


def register(target: Set) -> List[str]:
    """Add the notebook tools to a user-functions set when Cortex enables them.

    No-op (returns []) unless `CORTEX_NOTEBOOK_URL` is set in the environment
    — Cortex sets it only while its notebook kernel host is running, so the
    tools never appear in contexts where they can't work. No network calls
    are made here.

    :param target: The set of user-facing tool functions to mutate in place.
    :return: The names of the tools that were registered.
    :rtype: list
    """
    if not os.environ.get(ENV_VAR):
        return []
    for fn in (
        notebook_create,
        notebook_list,
        notebook_read,
        notebook_run,
        notebook_edit_cell,
        notebook_restart,
    ):
        target.add(fn)
    return list(_TOOL_NAMES)


SYSTEM_PROMPT_ADDENDUM = """\
## Cortex notebooks — runtime context

A Cortex notebook kernel host is running. The `notebook_*` tools let you do
real, stateful data work in Jupyter notebooks the user audits live in the
Cortex Notebook panel — every notebook is a persistent .ipynb artifact, not
ephemeral scratch.

| You want to...                              | Call |
|---------------------------------------------|------|
| Start a new analysis document               | `notebook_create(name)` |
| Find an existing notebook                   | `notebook_list()` |
| Add + run a code cell (state persists)      | `notebook_run(notebook_id, source)` |
| Add narrative between code cells            | `notebook_run(notebook_id, source, cell_type="markdown")` |
| Fix a cell in place (code reruns)           | `notebook_edit_cell(notebook_id, cell_id, source)` |
| Inspect the document / full outputs         | `notebook_read(notebook_id)` |
| Cheap structural overview (no outputs)      | `notebook_read(notebook_id, include_outputs=False)` |
| Recover a hung kernel / fresh state         | `notebook_restart(notebook_id)` |

Operating rules:

1. **Build documents, not scratchpads.** Interleave markdown cells with code
   so the finished notebook reads as a report the user can keep.
2. **Outputs are truncated to 8KB + a mime summary.** The full output lives
   in the notebook; `notebook_read` it back only when you actually need more.
3. **Kernel state persists across `notebook_run` calls** in the same
   notebook. `notebook_restart` wipes variables but never the document.
4. **Editing a cell does not re-run later cells** — re-run dependents
   yourself after `notebook_edit_cell`.
5. **A 409 means a cell is already running** in that notebook — wait and
   retry; don't stack executions.
"""


def system_prompt() -> str:
    """Return the notebook system-prompt addendum, or "" when not enabled.

    :return: The addendum string when `CORTEX_NOTEBOOK_URL` is set, else "".
    :rtype: str
    """
    if not os.environ.get(ENV_VAR):
        return ""
    return SYSTEM_PROMPT_ADDENDUM
