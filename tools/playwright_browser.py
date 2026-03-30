"""
Playwright browser automation — headless browser control.
Uses Playwright's sync API directly for reliable browser automation.
"""

import json
import os

# Lazy-loaded browser instance (shared across calls in the same session)
_browser = None
_page = None


def _get_page():
    """Get or create a headless browser page."""
    global _browser, _page
    if _page and not _page.is_closed():
        return _page

    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        _browser = pw.chromium.launch(headless=True)
        _page = _browser.new_page()
        return _page
    except ImportError:
        return None
    except Exception:
        return None


def browser_navigate(url: str) -> str:
    """
    Navigate the browser to a URL and return the page content summary.

    :param url: The URL to navigate to.
    :return: JSON with page title, URL, and text content excerpt.
    :rtype: str
    """
    page = _get_page()
    if not page:
        return json.dumps({"error": "Playwright not installed. Run: pip install playwright && playwright install chromium"})

    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
        title = page.title()
        text = page.inner_text("body")[:5000]
        return json.dumps({
            "url": page.url,
            "title": title,
            "status": response.status if response else None,
            "text": text,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def browser_click(selector: str) -> str:
    """
    Click an element on the current page using a CSS selector or text.

    :param selector: CSS selector or text content to click (e.g. "text=Submit", "#login-btn").
    :return: JSON result of the click action.
    :rtype: str
    """
    page = _get_page()
    if not page:
        return json.dumps({"error": "Playwright not installed."})

    try:
        page.click(selector, timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        return json.dumps({"clicked": selector, "url": page.url, "title": page.title()})
    except Exception as e:
        return json.dumps({"error": str(e)})


def browser_type_text(selector: str, text: str, submit: bool = False) -> str:
    """
    Type text into an input field on the page.

    :param selector: CSS selector for the input field (e.g. "#search", "input[name='q']").
    :param text: The text to type.
    :param submit: Whether to press Enter after typing (default False).
    :return: JSON result of the type action.
    :rtype: str
    """
    page = _get_page()
    if not page:
        return json.dumps({"error": "Playwright not installed."})

    try:
        page.fill(selector, text, timeout=10000)
        if submit:
            page.press(selector, "Enter")
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        return json.dumps({"typed": text, "selector": selector, "submitted": submit, "url": page.url})
    except Exception as e:
        return json.dumps({"error": str(e)})


def browser_snapshot() -> str:
    """
    Get the current page's text content and interactive elements.

    :return: JSON with page URL, title, text content, and links.
    :rtype: str
    """
    page = _get_page()
    if not page:
        return json.dumps({"error": "Playwright not installed."})

    try:
        title = page.title()
        text = page.inner_text("body")[:10000]
        links = page.eval_on_selector_all(
            "a[href]",
            "els => els.slice(0, 50).map(e => ({text: e.innerText.trim().slice(0, 80), href: e.href}))"
        )
        inputs = page.eval_on_selector_all(
            "input, textarea, select, button",
            "els => els.slice(0, 30).map(e => ({tag: e.tagName, type: e.type, name: e.name, id: e.id, text: e.innerText?.trim().slice(0, 40) || ''}))"
        )
        return json.dumps({
            "url": page.url,
            "title": title,
            "text": text,
            "links": links,
            "interactive_elements": inputs,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def browser_screenshot(filename: str = "/tmp/screenshot.png") -> str:
    """
    Take a screenshot of the current browser page.

    :param filename: Path to save the screenshot (default /tmp/screenshot.png).
    :return: JSON with the screenshot file path and page info.
    :rtype: str
    """
    page = _get_page()
    if not page:
        return json.dumps({"error": "Playwright not installed."})

    try:
        page.screenshot(path=filename, full_page=True)
        return json.dumps({
            "screenshot": filename,
            "url": page.url,
            "title": page.title(),
            "size_bytes": os.path.getsize(filename),
        })
    except Exception as e:
        return json.dumps({"error": str(e)})
