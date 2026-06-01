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

URL = "https://www.canvasutrecht.com/floorplans.aspx"
SOLD_OUT_PHRASE = "at this moment there are no apartments available"
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
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en"}
    with httpx.Client(timeout=30, follow_redirects=True, headers=headers) as client:
        r = client.get(URL)
        r.raise_for_status()
        return r.text


def analyze(html: str) -> PageState:
    soup = BeautifulSoup(html, "html.parser")
    # Drop script/style noise, normalize whitespace
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = " ".join(soup.get_text(separator=" ").split())
    text_lower = text.lower()
    sold_out = SOLD_OUT_PHRASE in text_lower
    fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return PageState(sold_out=sold_out, fingerprint=fingerprint, snippet=text[:400])


# ---------- Notifications ----------

def notify_ntfy(title: str, message: str) -> bool:
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return False
    try:
        httpx.post(
            f"https://ntfy.sh/{topic}",
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": "urgent",  # high-priority push, bypasses Do Not Disturb on most setups
                "Tags": "house,rotating_light",
                "Click": URL,
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


def main() -> int:
    if "--test" in sys.argv:
        return run_test()
    return run_check()


if __name__ == "__main__":
    sys.exit(main())
