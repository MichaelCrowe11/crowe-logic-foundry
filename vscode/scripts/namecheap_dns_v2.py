#!/usr/bin/env python3
"""Improved Namecheap DNS automation for crowecode.com.

Differences from v1:
- Reuses persistent session at ~/.crowe-playwright/ so user is already logged in.
- Removes the 4 stale parking records (A x2, AAAA x2 with the cowecode typo).
- Adds the 3 Vercel records (TXT _vercel, A @, CNAME www).
- Scrolls each panel into view before clicking, since Namecheap's DNS page
  has multiple ADD NEW RECORD buttons in different sections.
- Uses 90-second timeouts on individual interactions instead of 30s default.
- Clicks SAVE ALL CHANGES at the end automatically.
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

# Stale parking records on the existing crowecode.com config that need to go.
REMOVE_RECORDS = [
    {"type": "A Record",    "host": "crowecode.com", "value": "66.241.124.135"},
    {"type": "A Record",    "host": "www",           "value": "66.241.124.135"},
    {"type": "AAAA Record", "host": "cowecode.com",  "value": None},  # the typo
    {"type": "AAAA Record", "host": "www",           "value": None},
]


def log(msg: str) -> None:
    print(f"[namecheap-v2] {msg}", flush=True)


def wait_for_panel(page, timeout_s: int = 600) -> None:
    log("Waiting for HOST RECORDS panel (log in if prompted, then leave the window alone)")
    page.goto(DNS_URL, wait_until="domcontentloaded")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if "advancedns" in page.url and page.locator("text=HOST RECORDS").first.is_visible():
                log("HOST RECORDS panel is loaded.")
                page.locator("text=HOST RECORDS").first.scroll_into_view_if_needed()
                return
        except Exception:
            pass
        time.sleep(2)
    raise SystemExit("Timed out waiting for the DNS panel to load.")


def remove_record(page, rec: dict) -> bool:
    """Find a row matching type+host and click its Remove button."""
    log(f"Removing {rec['type']:<13} {rec['host']:<14}")
    rows = page.locator("tbody tr")
    n = rows.count()
    for i in range(n):
        row = rows.nth(i)
        try:
            text = row.inner_text(timeout=5000)
        except PWTimeout:
            continue
        host_ok = rec['host'] in text
        type_ok = rec['type'].split()[0] in text
        if host_ok and type_ok:
            try:
                row.locator("text=Remove").first.click(timeout=10000)
                log(f"  removed row {i}")
                return True
            except PWTimeout:
                log(f"  could not click Remove on row {i}")
                return False
    log(f"  no matching row for {rec['type']} {rec['host']}")
    return False


def add_record(page, rec: dict) -> bool:
    log(f"Adding   {rec['type']:<13} {rec['host']:<14} -> {rec['value'][:60]}")
    # Scroll to bring the HOST RECORDS section into view; Namecheap renders
    # ADD NEW RECORD per-section so we need to click the one nearest HOST RECORDS.
    page.locator("text=HOST RECORDS").first.scroll_into_view_if_needed()
    add_btn = page.get_by_role("button", name="ADD NEW RECORD").first
    try:
        add_btn.scroll_into_view_if_needed()
        add_btn.click(timeout=90000)
    except PWTimeout:
        log("  ! ADD NEW RECORD click timed out")
        return False

    row = page.locator("tbody tr").last
    try:
        row.locator("[data-testid='select-control']").first.click(timeout=20000)
        page.get_by_role("option", name=rec["type"], exact=True).click(timeout=20000)
        row.locator("input[name='host']").fill(rec["host"], timeout=20000)
        row.locator("input[name='address']").fill(rec["value"], timeout=20000)
        log("  fields filled")
        return True
    except PWTimeout as e:
        log(f"  ! field fill failed: {e}")
        return False


def save_all_changes(page) -> bool:
    log("Clicking SAVE ALL CHANGES")
    try:
        btn = page.get_by_role("button", name="SAVE ALL CHANGES").first
        btn.scroll_into_view_if_needed()
        btn.click(timeout=30000)
        log("  saved")
        return True
    except PWTimeout:
        log("  ! could not find/click SAVE ALL CHANGES")
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
            for rec in REMOVE_RECORDS:
                remove_record(page, rec)
                time.sleep(1)

            log("=== adding Vercel records ===")
            for rec in ADD_RECORDS:
                add_record(page, rec)
                time.sleep(1)

            saved = save_all_changes(page)
            if saved:
                log("All changes saved. Verify in the panel before closing.")
            else:
                log("Could not auto-save. Click SAVE ALL CHANGES manually.")

            log("Browser left open for verification. Close window when done.")
            page.wait_for_event("close", timeout=0)
        finally:
            ctx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
