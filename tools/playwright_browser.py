"""
Playwright browser automation tool — full browser control via MCP.
Wraps the @playwright/mcp server as a locally callable tool.
"""

import json
import subprocess


def browser_navigate(url: str) -> str:
    """
    Navigate the browser to a URL and return the page accessibility snapshot.
    Opens a browser if one isn't already running.

    :param url: The URL to navigate to.
    :return: JSON with page title and accessibility tree snapshot.
    :rtype: str
    """
    return _run_playwright_action("browser_navigate", {"url": url})


def browser_click(element: str, ref: str = "") -> str:
    """
    Click an element on the current page. Use the element description
    or the ref ID from a previous snapshot.

    :param element: Description of the element to click (e.g. "Submit button", "Login link").
    :param ref: Optional element ref from accessibility snapshot.
    :return: JSON result of the click action.
    :rtype: str
    """
    args = {"element": element}
    if ref:
        args["ref"] = ref
    return _run_playwright_action("browser_click", args)


def browser_type_text(element: str, text: str, submit: bool = False) -> str:
    """
    Type text into an input field on the page.

    :param element: Description of the input field (e.g. "Search box", "Email field").
    :param text: The text to type.
    :param submit: Whether to press Enter after typing (default False).
    :return: JSON result of the type action.
    :rtype: str
    """
    args = {"element": element, "text": text, "submit": submit}
    return _run_playwright_action("browser_type", args)


def browser_snapshot() -> str:
    """
    Get the current page's accessibility tree snapshot. This shows all
    interactive elements, text content, and their ref IDs for clicking.

    :return: JSON with the full page accessibility snapshot.
    :rtype: str
    """
    return _run_playwright_action("browser_snapshot", {})


def browser_screenshot(filename: str = "/tmp/screenshot.png") -> str:
    """
    Take a screenshot of the current browser page.

    :param filename: Path to save the screenshot (default /tmp/screenshot.png).
    :return: JSON with the screenshot file path.
    :rtype: str
    """
    return _run_playwright_action("browser_take_screenshot", {"raw": True})


def _run_playwright_action(tool_name: str, arguments: dict) -> str:
    """Bridge to the Playwright MCP server via npx."""
    try:
        # Use the MCP server's stdio interface via a subprocess
        mcp_input = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments}
        })

        result = subprocess.run(
            ["npx", "-y", "@playwright/mcp@latest", "--headless"],
            input=mcp_input,
            capture_output=True,
            text=True,
            timeout=30,
            cwd="/Users/crowelogic/Projects/crowe-logic-foundry",
        )

        if result.stdout:
            return result.stdout[:50000]
        return json.dumps({"note": "Action sent to Playwright", "tool": tool_name, "args": arguments})

    except subprocess.TimeoutExpired:
        return json.dumps({"error": f"Playwright action timed out: {tool_name}"})
    except Exception as e:
        return json.dumps({"error": str(e)})
