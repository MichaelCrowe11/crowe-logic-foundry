"""
Browser tool — fetch and extract content from web pages.
"""

import json
import httpx
from bs4 import BeautifulSoup


def browse_url(url: str, extract_mode: str = "text") -> str:
    """
    Fetch a web page and extract its content. Supports text extraction
    and raw HTML modes.

    :param url: The URL to fetch (must start with http:// or https://).
    :param extract_mode: "text" for readable text, "html" for raw HTML, "links" for all links.
    :return: JSON with the extracted page content.
    :rtype: str
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        response = httpx.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) CroweLogic/0.1",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            timeout=20,
            follow_redirects=True,
        )
        response.raise_for_status()

        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            return json.dumps({
                "url": str(response.url),
                "status": response.status_code,
                "content_type": "json",
                "body": response.json(),
            })

        soup = BeautifulSoup(response.text, "html.parser")

        # Remove script, style, nav, footer noise
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        if extract_mode == "html":
            body = str(soup)[:100000]
            return json.dumps({"url": str(response.url), "status": response.status_code, "html": body})

        if extract_mode == "links":
            links = []
            for a in soup.find_all("a", href=True):
                links.append({"text": a.get_text(strip=True)[:200], "href": a["href"]})
            return json.dumps({"url": str(response.url), "status": response.status_code, "links": links[:200]})

        # Default: text extraction
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        text = soup.get_text(separator="\n", strip=True)
        # Collapse multiple blank lines
        lines = [line for line in text.splitlines() if line.strip()]
        text = "\n".join(lines)
        if len(text) > 80000:
            text = text[:80000] + "\n... (content truncated)"

        return json.dumps({
            "url": str(response.url),
            "status": response.status_code,
            "title": title,
            "text": text,
        })
    except httpx.HTTPStatusError as e:
        return json.dumps({"error": f"HTTP {e.response.status_code}: {str(e)}", "url": url})
    except Exception as e:
        return json.dumps({"error": str(e), "url": url})
