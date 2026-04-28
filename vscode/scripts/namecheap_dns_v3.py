#!/usr/bin/env python3
"""Namecheap DNS automation for crowecode.com — v3 with row refetch.

v2 hit the row-index-collapse bug: removing row N shifts all subsequent
rows up by one, so the next removal targets the wrong record. v3 fixes
this by:
- Walking records by VALUE match (not index)
- Refetching the row list after every mutation
- Single-removal-per-pass loop until no targets remain

Idempotent: safe to run multiple times. Records already removed are
detected and skipped. Records already added are detected and skipped.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

DOMAIN = "crowecode.com"
DNS_URL = f"https://ap.www.namecheap.com/Domains/DomainControlPanel/{DOMAIN}/advancedns"
PROFILE = Path.home() / ".crowe-playwright"
PROFILE.mkdir(exist_ok=True)

VERIFY_TOKEN = "vc-domain-verify=crowecode.com,df1d7a44eccc23b353f0"

ADD_RECORDS = [
    {"type": "TXT Record",   "host": "_vercel", "value": VERIFY_TOKEN},
    {"type": "A Record",     "host": "@",       "value": "76.76.21.21"},
    {"type": "CNAME Record", "host": "www",     "value": "cname.vercel-dns.com."},
]

# A record will be deleted if its value contains any of these strings.
# Using value-match instead of host-match because host names can be ambiguous
# (the typo "cowecode.com" is missing an r so we match the bad IPv6 instead).
REMOVE_BY_VALUE_CONTAINS = [
    "66.241.124.135",         # Namecheap parking IPv4
    "2a09:8280:1::9b:736c:0", # Namecheap parking IPv6 (also covers the cowecode typo row)
]


def log(msg: str) -> None:
    print(f"[ncv3] {msg}", flush=True)


def wait_for_panel(page, timeout_s: int = 600) -> None:
    log("Waiting for HOST RECORDS panel")
    page.goto(DNS_URL, wait_until="domcontentloaded")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if "advancedns" in page.url and page.locator("text=HOST RECORDS").first.is_visible():
                page.locator("text=HOST RECORDS").first.scroll_into_view_if_needed()
                log("HOST RECORDS visible")
                return
        except Exception:
            pass
        time.sleep(2)
    raise SystemExit("Timed out waiting for DNS panel")


def find_remove_target(page) -> bool:
    """Find a row with any deletion-candidate value and click its Remove.

    Returns True if it removed one row this pass, False if no targets remain.
    """
    rows = page.locator("tbody tr")
    n = rows.count()
    for i in range(n):
        row = rows.nth(i)
        try:
            text = row.inner_text(timeout=3000)
        except PWTimeout:
            continue
        if any(needle in text for needle in REMOVE_BY_VALUE_CONTAINS):
            try:
                row.locator("text=Remove").first.click(timeout=10000)
                preview = " ".join(text.split()[:6])
                log(f"removed row: {preview}")
                return True
            except PWTimeout:
                log(f"could not click Remove on row {i}")
                return False
    return False


def remove_all_stale(page, max_passes: int = 8) -> int:
    """Loop find_remove_target until no targets remain or budget exceeded."""
    removed = 0
    for _ in range(max_passes):
        if find_remove_target(page):
            removed += 1
            time.sleep(0.8)  # let the table reflow
        else:
            break
    return removed


def record_already_present(page, rec: dict) -> bool:
    """Check if a record with this host+value is already in the table."""
    rows = page.locator("tbody tr")
    n = rows.count()
    for i in range(n):
        try:
            text = rows.nth(i).inner_text(timeout=3000)
        except PWTimeout:
            continue
        if rec["host"] in text and rec["value"][:40] in text:
            return True
    return False


def add_record(page, rec: dict) -> bool:
    if record_already_present(page, rec):
        log(f"already present: {rec['type']} {rec['host']}")
        return True

    log(f"adding {rec['type']} {rec['host']} -> {rec['value'][:50]}")
    page.locator("text=HOST RECORDS").first.scroll_into_view_if_needed()
    add_btn = page.get_by_role("button", name="ADD NEW RECORD").first
    try:
        add_btn.scroll_into_view_if_needed()
        add_btn.click(timeout=60000)
    except PWTimeout:
        log("  ! ADD NEW RECORD click timed out")
        return False

    # The new row is the last one in tbody after the click
    time.sleep(0.5)
    row = page.locator("tbody tr").last
    try:
        row.locator("[data-testid='select-control']").first.click(timeout=15000)
        page.get_by_role("option", name=rec["type"], exact=True).click(timeout=15000)
        row.locator("input[name='host']").fill(rec["host"], timeout=15000)
        row.locator("input[name='address']").fill(rec["value"], timeout=15000)
        log("  fields filled")
        return True
    except PWTimeout as e:
        log(f"  ! field fill failed: {e}")
        return False


def save_all_changes(page) -> bool:
    log("clicking SAVE ALL CHANGES")
    try:
        btn = page.get_by_role("button", name="SAVE ALL CHANGES").first
        btn.scroll_into_view_if_needed()
        btn.click(timeout=30000)
        log("saved")
        return True
    except PWTimeout:
        log("could not click SAVE ALL CHANGES")
        return False


def main() -> int:
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            channel="chrome",
            headless=False,
            viewport={"width": 1440, "height": 1000},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(45000)
        try:
            wait_for_panel(page)

            log("=== removing stale records ===")
            removed = remove_all_stale(page)
            log(f"removed {removed} stale row(s)")

            log("=== adding Vercel records ===")
            added = 0
            for rec in ADD_RECORDS:
                if add_record(page, rec):
                    added += 1
                time.sleep(0.6)
            log(f"added {added}/{len(ADD_RECORDS)} records")

            saved = save_all_changes(page)
            log(f"save status: {'ok' if saved else 'manual'}")
            log("Browser left open for verification. Close window when done.")
            page.wait_for_event("close", timeout=0)
        finally:
            ctx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
