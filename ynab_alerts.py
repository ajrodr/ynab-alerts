#!/usr/bin/env python3
"""Daily YNAB summary and overspending alerts, sent as push notifications via ntfy.

Meant to run every hour from a scheduler (see .github/workflows/ynab-alerts.yml).
Each run decides for itself whether anything should be sent:

* Once a day, at SUMMARY_HOUR local time, it sends a summary: pending
  transactions, transactions needing approval, uncategorized transactions and
  overspent categories.
* On every other run it sends an alert only for categories that have become
  overspent since the last message.

Configuration is read from environment variables; see README.md.
Uses only the Python standard library.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

YNAB_API = "https://api.ynab.com/v1"

# Category groups that never represent spending.
SKIP_GROUPS = {"Internal Master Category", "Credit Card Payments", "Hidden Categories"}

# Late summaries (GitHub delays, lost state) are skipped after this many hours.
SUMMARY_WINDOW_HOURS = 4
MAX_LISTED = 5


def env(name, default=None):
    value = os.environ.get(name, "").strip()
    return value or default


def require_env(name):
    value = env(name)
    if not value:
        sys.exit(f"Missing required environment variable: {name}")
    return value


def http(method, url, headers=None, data=None):
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        raise RuntimeError(f"{method} {url.split('?')[0]} failed: HTTP {e.code} {detail}") from None
    return json.loads(body) if body else None


# --- YNAB --------------------------------------------------------------------


class YNAB:
    def __init__(self, token, budget_id="last-used"):
        self.token = token
        self.budget_id = budget_id

    def get(self, path, **params):
        url = f"{YNAB_API}/budgets/{self.budget_id}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return http("GET", url, {"Authorization": f"Bearer {self.token}"})["data"]

    def category_groups(self):
        return self.get("/categories")["category_groups"]

    def transactions(self, **params):
        return [t for t in self.get("/transactions", **params)["transactions"] if not t.get("deleted")]


@dataclass
class Stats:
    pending: int
    unapproved: int
    uncategorized: int
    overspent: list = field(default_factory=list)  # [{"id", "name", "over"}], worst first


def overspent_categories(groups):
    """Categories whose Available balance is negative (YNAB's red "overspent")."""
    result = []
    for group in groups:
        if group.get("hidden") or group.get("deleted") or group["name"] in SKIP_GROUPS:
            continue
        for cat in group["categories"]:
            if cat.get("hidden") or cat.get("deleted"):
                continue
            over = -cat["balance"]
            if over > 0:
                result.append({"id": cat["id"], "name": cat["name"], "over": over})
    return sorted(result, key=lambda c: -c["over"])


def collect_stats(ynab, today, lookback_days):
    since = (today - timedelta(days=lookback_days)).isoformat()
    recent = ynab.transactions(since_date=since)
    return Stats(
        pending=sum(1 for t in recent if t["cleared"] == "uncleared"),
        unapproved=len(ynab.transactions(type="unapproved")),
        uncategorized=len(ynab.transactions(type="uncategorized")),
        overspent=overspent_categories(ynab.category_groups()),
    )


# --- Messages ----------------------------------------------------------------


def money(milliunits):
    return f"${abs(milliunits) / 1000:,.2f}"


def list_categories(cats):
    shown = [f"{c['name']} {money(c['over'])}" for c in cats[:MAX_LISTED]]
    if len(cats) > MAX_LISTED:
        shown.append(f"+{len(cats) - MAX_LISTED} more")
    return ", ".join(shown)


def summary_message(today, stats):
    lines = [
        f"YNAB daily - {today:%a %b} {today.day}",
        f"Pending: {stats.pending}",
        f"Need approval: {stats.unapproved}",
        f"Uncategorized: {stats.uncategorized}",
        f"Over budget: {list_categories(stats.overspent) if stats.overspent else 'none'}",
    ]
    return "YNAB daily summary", "\n".join(lines)


def alert_message(new_overspent):
    lines = ["YNAB alert - over budget:"]
    lines += [f"{c['name']}: over by {money(c['over'])}" for c in new_overspent[:MAX_LISTED]]
    if len(new_overspent) > MAX_LISTED:
        lines.append(f"+{len(new_overspent) - MAX_LISTED} more")
    return "YNAB over budget", "\n".join(lines)


def plan(now, state, stats, summary_hour, force_summary=False):
    """Decide what to send. Returns (messages, new_state); pure, no I/O."""
    today = now.date().isoformat()
    month = now.strftime("%Y-%m")
    overspent_ids = {c["id"] for c in stats.overspent}

    # Forget categories that are no longer overspent so a relapse alerts again.
    alerted = set() if state.get("month") != month else set(state.get("alerted", []))
    alerted &= overspent_ids
    new = [c for c in stats.overspent if c["id"] not in alerted]

    in_window = summary_hour <= now.hour < summary_hour + SUMMARY_WINDOW_HOURS
    due = in_window and state.get("summary_sent") != today

    messages = []
    new_state = dict(state, month=month)
    if force_summary or due:
        messages.append(summary_message(now.date(), stats))
        new_state["summary_sent"] = today
        alerted = overspent_ids  # the summary already lists them
    elif new:
        messages.append(alert_message(new))
        alerted |= {c["id"] for c in new}
    new_state["alerted"] = sorted(alerted)
    return messages, new_state


# --- Sending -----------------------------------------------------------------


def send_ntfy(title, body):
    server = env("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    headers = {"Title": title, "Tags": "moneybag"}
    if env("NTFY_TOKEN"):
        headers["Authorization"] = f"Bearer {env('NTFY_TOKEN')}"
    http("POST", f"{server}/{require_env('NTFY_TOPIC')}", headers, body.encode())


# --- Main --------------------------------------------------------------------


def load_state(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print messages instead of sending them")
    parser.add_argument("--force-summary", action="store_true", help="send the daily summary now")
    args = parser.parse_args(argv)

    tz = ZoneInfo(env("TIMEZONE", "America/Chicago"))
    now = datetime.now(tz)
    summary_hour = int(env("SUMMARY_HOUR", "7"))
    if now.hour < summary_hour and not args.force_summary:
        print(f"{now:%H:%M} is before {summary_hour}:00; quiet hours, nothing to do.")
        return 0

    if not args.dry_run:
        require_env("NTFY_TOPIC")

    ynab = YNAB(require_env("YNAB_TOKEN"), env("YNAB_BUDGET_ID", "last-used"))
    stats = collect_stats(ynab, now.date(), int(env("PENDING_LOOKBACK_DAYS", "7")))

    state_path = Path(env("STATE_FILE", ".state/state.json"))
    messages, new_state = plan(now, load_state(state_path), stats, summary_hour, args.force_summary)
    if not messages:
        print("Nothing new to report.")
        return 0

    if args.dry_run:
        for title, body in messages:
            print(f"--- {title} ---\n{body}\n")
        return 0

    # If sending fails this raises before the state is saved, so the next run retries.
    for title, body in messages:
        send_ntfy(title, body)
        print(f"Sent: {title}")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(new_state))
    return 0


if __name__ == "__main__":
    sys.exit(main())
