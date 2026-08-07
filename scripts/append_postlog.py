#!/usr/bin/env python3
"""
Append scheduled-post rows to the PostLog tab of the HPH Operations Tracker.

Usage (from the routine, after Blotato scheduling succeeds):
    python scripts/append_postlog.py postlog_rows.json

postlog_rows.json format: a JSON array of rows, one per scheduled post:
    [
      ["2026-08-08", "Cajun Salmon Bowl", "Breakfast", "@coach.macro", "08:03"],
      ...
    ]
Columns map to PostLog A-E: Date, Recipe, Slot, Account, Sched Time (ET).
Column F (Posted?) and G-K (metrics) are left blank for VA backfill.

Auth: Google service account. The script looks for credentials in this order:
  1. env GOOGLE_SA_KEY_B64  (base64-encoded service account JSON)  <- preferred
  2. env GOOGLE_SA_KEY_JSON (raw JSON string)
  3. file secrets/sa-key.json in the repo root                     <- fallback

Behavior:
  - Skips rows already present in the sheet (same date + account + slot),
    so a manual re-run of the routine cannot create duplicate rows.
  - Warns when the log passes row 1100: dashboard formulas cover rows 5-1205
    (about 42 days at 28 posts/day). Extend the ranges or archive before then.
  - Exits non-zero on failure so the routine can log SHEETS-ERROR and move on.
    A Sheets failure must never block Blotato posting; call this script last.

Dependencies: pip install gspread google-auth
"""

import base64
import json
import os
import sys

import gspread
from google.oauth2.service_account import Credentials

SHEET_ID = os.environ.get(
    "HPH_SHEET_ID", "1feoqMkpPLQknuWWZxvkalvBHdoboCQDGftwC-7JRN34"
)
TAB_NAME = "PostLog"
DATA_START_ROW = 5      # row 4 is headers
FORMULA_CEILING = 1205  # dashboard formulas reference PostLog rows 5-1205
WARN_ROW = 1100
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def load_credentials():
    b64 = os.environ.get("GOOGLE_SA_KEY_B64")
    raw = os.environ.get("GOOGLE_SA_KEY_JSON")
    if b64:
        info = json.loads(base64.b64decode(b64))
    elif raw:
        info = json.loads(raw)
    elif os.path.exists("secrets/sa-key.json"):
        with open("secrets/sa-key.json") as fh:
            info = json.load(fh)
    else:
        sys.exit(
            "SHEETS-ERROR: no service account key found "
            "(GOOGLE_SA_KEY_B64, GOOGLE_SA_KEY_JSON, or secrets/sa-key.json)"
        )
    return Credentials.from_service_account_info(info, scopes=SCOPES)


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: append_postlog.py postlog_rows.json")

    with open(sys.argv[1]) as fh:
        rows = json.load(fh)
    if not rows:
        print("no rows to append, exiting cleanly")
        return

    # basic shape check before touching the sheet
    for r in rows:
        if not (isinstance(r, list) and len(r) == 5):
            sys.exit(f"SHEETS-ERROR: bad row shape (need 5 cols A-E): {r!r}")

    gc = gspread.authorize(load_credentials())
    ws = gc.open_by_key(SHEET_ID).worksheet(TAB_NAME)

    # existing (date, account, slot) keys for dedupe
    existing = ws.get_values(f"A{DATA_START_ROW}:E{FORMULA_CEILING}")
    seen = {(row[0], row[3], row[2]) for row in existing if row and row[0]}

    new_rows = [r for r in rows if (r[0], r[3], r[2]) not in seen]
    skipped = len(rows) - len(new_rows)
    if skipped:
        print(f"dedupe: skipped {skipped} row(s) already in the sheet")
    if not new_rows:
        print("all rows already present, nothing appended")
        return

    ws.append_rows(
        new_rows,
        value_input_option="USER_ENTERED",  # so dates parse as dates
        table_range=f"A{DATA_START_ROW}",
    )
    print(f"appended {len(new_rows)} row(s) to {TAB_NAME}")

    last_row = len([r for r in existing if r and r[0]]) + DATA_START_ROW - 1 + len(new_rows)
    if last_row >= WARN_ROW:
        print(
            f"WARNING: PostLog is at ~row {last_row}. Dashboard formulas stop at "
            f"row {FORMULA_CEILING}. Extend the formula ranges or archive old rows soon."
        )


if __name__ == "__main__":
    main()
