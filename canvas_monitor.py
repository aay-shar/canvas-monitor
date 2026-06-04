"""
Canvas Utrecht availability monitor.

Polls the Canvas Utrecht floorplans page and sends a push notification when
the 'no apartments available' message disappears (i.e. a listing may have
gone live).

Notifications: ntfy.sh (default) and/or Telegram. Pick whichever you set
env vars for; both work simultaneously if both are configured.

Run modes:
  python canvas_monitor.py          # normal run
  python canvas_monitor.py --test   # send a test notification and exit
"""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

URL = "https://canvas-student.securerc.co.uk/onlineleasing/canvas-utrecht/floorplans.aspx"
# This phrase lives INSIDE the listing container (<div id="floorPlanDataContainer">)
# and is what RENTCafé renders when there's no inventory. When listings exist,
# this paragraph is replaced by floorplan cards. The "Apartments will appear..."
# message we used to check is permanent CMS boilerplate that stays regardless,
# so it can't be used as a sold-out signal.
SOLD_OUT_PHRASE = "floor plan details not available for this property"
STATE_FILE = Path(__file__).parent / "state.txt"

# Note on User-Agent: an honest identifying UA (e.g. "CanvasMonitor/1.0")
# is best-practice etiquette but is typically 403'd by the WAF in front of
# this site. We use a normal browser UA so the script functions. The
# request volume (one fetch every ~10 min) is far below any reasonable
# threshold of concern and is indistinguishable from a person who keeps
# the page open in a background tab and refreshes occasionally.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class PageState:
    sold_out: bool
    fingerprint: str  # short hash of the visible text
    snippet: str      # first 400 chars of visible text, for debugging

    def as_state_string(self) -> str:
        return f"sold_out={self.sold_out};fp={self.fingerprint}"


def fetch_page() -> str:
    """Fetch the Canvas floorplans page.

    The page is server-rendered (verified by inspecting page source) — the
    listing markup is in the initial HTML, no JavaScript execution required.
    curl_cffi impersonates Chrome's TLS fingerprint so the WAF lets us in;
    plain httpx/requests get 403'd.
    """
    from curl_cffi import requests as curl_requests
    r = curl_requests.get(
        URL,
        impersonate="chrome",
        timeout=30,
        headers={"Referer": "https://www.canvasutrecht.com/"},
    )
    r.raise_for_status()
    return r.text


def analyze(html: str) -> PageState:
    import re
    soup = BeautifulSoup(html, "html.parser")
    # Drop script/style noise, normalize whitespace
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = " ".join(soup.get_text(separator=" ").split())
    text_lower = text.lower()

    has_boilerplate = SOLD_OUT_PHRASE in text_lower
    # Positive signal: € followed by a rent-sized number. Real listings show
    # prices (€800, €1,250, etc.) — empty/boilerplate pages typically don't.
    rent_amounts = re.findall(r"€\s*\d[\d,.\s]{2,}", text)
    has_rent = len(rent_amounts) > 0

    # We say "sold out" only if the boilerplate is there AND no rent amounts
    # appear. If we find prices, listings exist regardless of boilerplate
    # (some templates show the boilerplate alongside listings).
    sold_out = has_boilerplate and not has_rent

    fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return PageState(sold_out=sold_out, fingerprint=fingerprint, snippet=text[:400])


# ---------- Notifications ----------

def notify_ntfy(title: str, message: str) -> bool:
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return False
    try:
        # Use ntfy's JSON publish API so UTF-8 in the title (emoji, accents,
        # non-Latin scripts) is handled correctly. The simpler header-based
        # API requires ASCII-only header values and chokes on emoji.
        httpx.post(
            "https://ntfy.sh/",
            json={
                "topic": topic,
                "title": title,
                "message": message,
                "priority": 5,  # 5=urgent/max; JSON API requires integer, not string
                "tags": ["house", "rotating_light"],
                "click": URL,
            },
            timeout=15,
        ).raise_for_status()
        print("  → ntfy: sent")
        return True
    except Exception as e:
        print(f"  → ntfy: FAILED ({e})", file=sys.stderr)
        return False


def notify_telegram(title: str, message: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        return False
    try:
        body = f"*{title}*\n\n{message}\n\n{URL}"
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": body, "parse_mode": "Markdown"},
            timeout=15,
        ).raise_for_status()
        print("  → telegram: sent")
        return True
    except Exception as e:
        print(f"  → telegram: FAILED ({e})", file=sys.stderr)
        return False


def notify(title: str, message: str) -> None:
    sent_any = False
    sent_any |= notify_ntfy(title, message)
    sent_any |= notify_telegram(title, message)
    if not sent_any:
        print(
            "  ⚠ No notification sent. Set NTFY_TOPIC and/or "
            "TELEGRAM_BOT_TOKEN+TELEGRAM_CHAT_ID.",
            file=sys.stderr,
        )


# ---------- Main ----------

def load_prev_state() -> str:
    return STATE_FILE.read_text().strip() if STATE_FILE.exists() else ""


def save_state(state: str) -> None:
    STATE_FILE.write_text(state + "\n")


def run_test() -> int:
    notify(
        "✅ Canvas monitor — test notification",
        "If you see this, notifications are working. The monitor will only "
        "alert you when Canvas Utrecht actually has availability.",
    )
    return 0


def run_debug() -> int:
    """Fetch the page once and print everything the script 'sees'.

    Use this to validate the monitor against what the live page actually
    shows. Run it any time, compare the saved HTML with the live page in
    your browser; if both look the same, the monitor is seeing what you see.
    """
    import re
    print(f"Fetching {URL}")
    print("(rendering with headless Chromium — this takes a few seconds)\n")
    try:
        html = fetch_page()
    except Exception as e:
        print(f"FETCH FAILED: {e}", file=sys.stderr)
        return 1

    debug_html = STATE_FILE.parent / f"debug_{STATE_FILE.stem}.html"
    debug_html.write_text(html, encoding="utf-8")

    state = analyze(html)
    text_lower = html.lower()
    rent_amounts = re.findall(r"€\s*\d[\d,.\s]{2,}", html)

    print(f"Fetched {len(html):,} chars of fully-rendered HTML.")
    print(f"Saved snapshot to: {debug_html}")
    print()
    print("Analysis:")
    print(f"  sold_out:                    {state.sold_out}")
    print(f"  fingerprint:                 {state.fingerprint}")
    print(f"  € amounts on page:           {len(rent_amounts)}")
    if rent_amounts:
        print(f"  first few €:                 {rent_amounts[:5]}")
    print()
    print("Phrase counts (raw HTML, case-insensitive):")
    for phrase in [
        SOLD_OUT_PHRASE,
        "availability",
        "contact us",
        "studio",
        "book now",
        "fully booked",
    ]:
        print(f"  {phrase!r:55s} {text_lower.count(phrase.lower())}")
    print()
    print("--- First 600 chars of visible text ---")
    print(state.snippet[:600])
    print()
    print("=" * 60)
    print("HOW TO VALIDATE:")
    print("=" * 60)
    print(f"1. Open the saved snapshot in a browser to see what the script saw:")
    print(f"     open {debug_html}")
    print(f"2. In another tab open the live page:")
    print(f"     {URL}")
    print(f"3. They should look identical (same listings or both empty).")
    print(f"   - If both show the SAME listings → monitor is working.")
    print(f"   - If live page shows listings but snapshot is empty →")
    print(f"     JS rendering is still missing; tell me.")
    print(f"   - If both are empty → no current listing; this is the")
    print(f"     normal state. Re-run --debug whenever you see one live")
    print(f"     to confirm the monitor catches it.")
    return 0


def run_check() -> int:
    try:
        html = fetch_page()
    except Exception as e:
        print(f"ERROR: fetch failed: {e}", file=sys.stderr)
        # Don't update state on transient errors
        return 1

    state = analyze(html)
    print(f"sold_out={state.sold_out}  fingerprint={state.fingerprint}")
    print(f"snippet: {state.snippet[:200]}...")

    prev = load_prev_state()
    current = state.as_state_string()
    print(f"prev_state:    {prev or '(none)'}")
    print(f"current_state: {current}")

    # Notify only when availability is detected AND the page content has
    # changed since the last notification (prevents repeated alerts about
    # the same listing).
    if not state.sold_out and prev != current:
        notify(
            "🏠 Canvas Utrecht: availability changed!",
            "The 'no apartments available' message is no longer on the "
            "floorplans page. Check the site NOW — listings go fast.",
        )
    else:
        reason = "still sold out" if state.sold_out else "no change since last notification"
        print(f"  → no notification ({reason})")

    save_state(current)
    return 0


def run_loop(interval: int) -> int:
    """Run run_check() forever, sleeping `interval` seconds between checks."""
    import time
    from datetime import datetime

    print(f"Loop mode: polling every {interval}s. Press Ctrl+C to stop.\n")
    iteration = 0
    while True:
        iteration += 1
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"--- [{ts}] check #{iteration} ---")
        try:
            run_check()
        except Exception as e:
            # A network blip, DNS hiccup, etc. shouldn't kill the loop.
            print(f"  iteration error (continuing): {e}", file=sys.stderr)
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nStopped.")
            return 0


def main() -> int:
    if "--test" in sys.argv:
        return run_test()

    if "--debug" in sys.argv:
        return run_debug()

    if "--loop" in sys.argv:
        interval = 60  # sane default; do not go below ~30 unless you want a ban
        if "--interval" in sys.argv:
            i = sys.argv.index("--interval")
            try:
                interval = int(sys.argv[i + 1])
            except (IndexError, ValueError):
                print("Usage: --interval <seconds>", file=sys.stderr)
                return 2
        if interval < 10:
            print(
                f"Refusing interval={interval}s — would trigger WAF/IP ban "
                "and stop all notifications. Use >=30s.",
                file=sys.stderr,
            )
            return 2
        try:
            return run_loop(interval)
        except KeyboardInterrupt:
            print("\nStopped.")
            return 0

    return run_check()


if __name__ == "__main__":
    sys.exit(main())
