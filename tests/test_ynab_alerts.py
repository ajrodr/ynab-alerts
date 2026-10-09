import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ynab_alerts as ya

CHI = ZoneInfo("America/Chicago")


def cat(id, name, budgeted=0, activity=0, balance=0, **extra):
    return {"id": id, "name": name, "budgeted": budgeted, "activity": activity, "balance": balance, **extra}


GROUPS = [
    {"name": "Everyday", "categories": [
        cat("dining", "Dining Out", budgeted=100_000, activity=-142_100, balance=-42_100),
        cat("groceries", "Groceries", budgeted=500_000, activity=-300_000, balance=200_000),
        cat("old", "Old", balance=-5_000, hidden=True),
    ]},
    {"name": "Credit Card Payments", "categories": [cat("visa", "Visa", balance=-1_000)]},
]


def stats(*overspent_ids):
    over = [{"id": i, "name": i.title(), "over": 1_000} for i in overspent_ids]
    return ya.Stats(pending=2, unapproved=3, uncategorized=1, overspent=over)


class OverspentTests(unittest.TestCase):
    def test_negative_available_skipping_hidden_and_credit_cards(self):
        result = ya.overspent_categories(GROUPS)
        self.assertEqual([(c["id"], c["over"]) for c in result], [("dining", 42_100)])


class MessageTests(unittest.TestCase):
    def test_money(self):
        self.assertEqual(ya.money(-1_234_560), "$1,234.56")

    def test_summary(self):
        s = ya.Stats(pending=2, unapproved=3, uncategorized=1,
                     overspent=[{"id": "d", "name": "Dining Out", "over": 42_100}])
        _, body = ya.summary_message(date(2026, 10, 9), s)
        self.assertEqual(body, "YNAB daily - Fri Oct 9\nPending: 2\nNeed approval: 3\n"
                               "Uncategorized: 1\nOver budget: Dining Out $42.10")

    def test_long_lists_are_truncated(self):
        cats = [{"id": str(i), "name": f"C{i}", "over": 1000} for i in range(7)]
        self.assertTrue(ya.list_categories(cats).endswith("+2 more"))


class PlanTests(unittest.TestCase):
    def at(self, hour, day=9):
        return datetime(2026, 10, day, hour, 5, tzinfo=CHI)

    def test_summary_sent_once_per_day(self):
        msgs, state = ya.plan(self.at(7), {}, stats("dining"), 7)
        self.assertEqual([t for t, _ in msgs], ["YNAB daily summary"])
        msgs, _ = ya.plan(self.at(8), state, stats("dining"), 7)
        self.assertEqual(msgs, [])  # dining was already in the summary

    def test_summary_not_sent_late_in_day(self):
        msgs, _ = ya.plan(self.at(20), {}, stats(), 7)
        self.assertEqual(msgs, [])

    def test_new_overspending_alerts_once(self):
        state = {"month": "2026-10", "summary_sent": "2026-10-09", "alerted": []}
        msgs, state = ya.plan(self.at(13), state, stats("dining"), 7)
        self.assertEqual(msgs[0][0], "YNAB over budget")
        msgs, state = ya.plan(self.at(14), state, stats("dining"), 7)
        self.assertEqual(msgs, [])

    def test_recovered_category_alerts_again_if_overspent_later(self):
        state = {"month": "2026-10", "summary_sent": "2026-10-09", "alerted": ["dining"]}
        _, state = ya.plan(self.at(13), state, stats(), 7)
        msgs, _ = ya.plan(self.at(14), state, stats("dining"), 7)
        self.assertEqual(len(msgs), 1)

    def test_new_month_resets_alerts(self):
        state = {"month": "2026-09", "summary_sent": "2026-10-09", "alerted": ["dining"]}
        msgs, _ = ya.plan(self.at(13), state, stats("dining"), 7)
        self.assertEqual(len(msgs), 1)

    def test_force_summary(self):
        state = {"month": "2026-10", "summary_sent": "2026-10-09", "alerted": []}
        msgs, _ = ya.plan(self.at(22), state, stats(), 7, force_summary=True)
        self.assertEqual(msgs[0][0], "YNAB daily summary")


class DeliverTests(unittest.TestCase):
    def test_one_failing_channel_does_not_block_the_other(self):
        sent = []

        def broken(title, body):
            raise RuntimeError("down")

        ok, errors = ya.deliver([("t", "b")], {"twilio": broken, "ntfy": lambda t, b: sent.append(t)})
        self.assertTrue(ok)
        self.assertEqual(sent, ["t"])
        self.assertEqual(len(errors), 1)


if __name__ == "__main__":
    unittest.main()
