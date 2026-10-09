# YNAB alerts

Daily YNAB summary and over-budget alerts, sent as a **real text (SMS via Twilio)** and/or a
**free push notification (ntfy)**, run on a schedule by GitHub Actions. No server needed.

**7:00 am Central — daily summary**
```
YNAB daily - Fri Oct 9
Pending: 3
Need approval: 5
Uncategorized: 2
Over budget: Dining Out $42.10
```

**Hourly, 7am–10pm — only when a category newly goes over budget**
```
YNAB alert - over budget:
Gas: over by $12.40
```

| Line | Meaning |
|---|---|
| Pending | Uncleared transactions from the last 30 days (usually bank-pending charges) |
| Need approval | Imported transactions you haven't approved yet in YNAB |
| Uncategorized | Transactions with no category |
| Over budget | Categories with a negative Available balance (YNAB's red), worst first |

Each over-budget category triggers **one** alert per month. If it recovers (you move money to it)
and later goes over again, you get another alert. Categories already listed in the morning summary
don't trigger a separate alert.

## Setup (about 20 minutes)

### 1. YNAB token
YNAB → **Account Settings → Developer Settings → New Token**. Copy it; it's shown only once.

### 2. Push notifications with ntfy (free)
1. Install the **ntfy** app on both phones (iOS / Android).
2. Make up a long, unguessable topic name, e.g. `ynab-rodr-7f3k9q2x8m`. Anyone who knows the
   name can read the messages, so treat it like a password.
3. In the app on **both** phones: **+ → Subscribe to topic →** enter that name.

### 3. Real SMS with Twilio (free trial for testing)
Because you're on AT&T (which shut down its free email-to-text gateway in 2025), real SMS needs
a paid sender. The Twilio free trial is enough to test it:
1. Sign up at twilio.com. The trial includes free credit.
2. **Verify both phone numbers** (yours and your wife's) under *Phone Numbers → Verified Caller IDs*.
   A trial account can only text verified numbers.
3. Get a trial phone number from the console.
4. Note the **Account SID** and **Auth Token** from the console dashboard.

Trial messages begin with "Sent from your Twilio trial account". To keep using SMS after the
trial, upgrade, buy a **toll-free number**, and submit Twilio's free *toll-free verification*
form (it takes a few days; US carriers block unverified senders). The ongoing cost is about
$2–3/month for one number and roughly 60 texts. If push notifications turn out to be enough,
just skip this step or remove the Twilio secrets.

### 4. GitHub
Keep this repository **private**. Private repos get 2,000 free Actions minutes/month, and this
job uses about 500.

Under **Settings → Secrets and variables → Actions → Secrets**, add:

| Secret | Value |
|---|---|
| `YNAB_TOKEN` | from step 1 |
| `NTFY_TOPIC` | from step 2 (leave out to disable push) |
| `TWILIO_ACCOUNT_SID` | from step 3 (leave out to disable SMS) |
| `TWILIO_AUTH_TOKEN` | from step 3 |
| `TWILIO_FROM` | your Twilio number, e.g. `+15125550100` |
| `TWILIO_TO` | both numbers, comma-separated: `+15125550111,+15125550122` |
| `YNAB_BUDGET_ID` | optional; defaults to your last-used budget |

Optional settings go under **Variables**:

| Variable | Default | |
|---|---|---|
| `SUMMARY_HOUR` | `7` | Hour for the daily summary |
| `TIMEZONE` | `America/Chicago` | Handles CST/CDT automatically |
| `OVERSPEND_RULE` | `available` | `available` = YNAB's red (Available < 0). `assigned` = this month's spending > this month's assigned amount (also flags categories you're intentionally drawing down from savings, like annual insurance) |
| `PENDING_LOOKBACK_DAYS` | `30` | How far back to count uncleared transactions |

### 5. Test it
The workflow must be on the repo's **default branch** (`main`) before GitHub will run it on a
schedule or show the *Run workflow* button.

**Actions → YNAB alerts → Run workflow**:
- Check **dry_run** first to see the message in the log without sending anything.
- Then run it with only **force_summary** checked. Both phones should get the push and the text.

## Running locally
```bash
export YNAB_TOKEN=...      # plus NTFY_TOPIC / TWILIO_* to actually send
python3 ynab_alerts.py --dry-run --force-summary
python3 -m unittest discover -s tests
```
Only the Python standard library is used (Python 3.9+).

## Notes
- GitHub's scheduler can start a run 5–20 minutes late, so the summary arrives around 7:05–7:30.
- GitHub disables scheduled workflows after 60 days with no commits **in public repos only**,
  which is another reason to keep this repo private.
- Run state (what was already sent) is kept in the Actions cache. If it's ever lost, the worst
  case is one duplicate message.
