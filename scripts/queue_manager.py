"""
Handles the "2 triggers/day but only 1 successful story/day" rule.

IMPORTANT FIX: this script used to mark a topic 'done' and record today's date BEFORE
story generation even ran. If Sarvam (or anything downstream) failed, the topic was still
silently marked done and the day's slot burned -- next trigger would just move to the NEXT
topic, permanently skipping the one that actually failed. Now:
  - This script ONLY picks a topic. It does not touch topics.csv and does not record
    "success" anywhere.
  - Skip-if-already-done-today is checked against `last_success_date`, which is only ever
    written by scripts/finalize_success.py -- and only after the upload step has actually
    succeeded. So if the morning run fails, the evening run retries the SAME topic instead
    of silently skipping it or burning it.
  - Dates use Asia/Kolkata (IST), not the GitHub Actions runner's UTC clock -- otherwise
    "one story per day" drifts by 5.5 hours from what you actually mean by "a day" in India.
"""
import csv, json, os, sys, datetime
from zoneinfo import ZoneInfo

STATUS_FILE = "queue/status.json"
TOPICS_FILE = "topics.csv"
IST = ZoneInfo("Asia/Kolkata")

def today_ist() -> str:
    return datetime.datetime.now(IST).date().isoformat()

def load_status():
    if os.path.exists(STATUS_FILE):
        with open(STATUS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"last_success_date": None}

def get_next_topic():
    with open(TOPICS_FILE, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        if row.get("status", "").strip().lower() == "pending":
            return row
    return None

def main():
    today = today_ist()
    status = load_status()

    if status.get("last_success_date") == today:
        print(f"Already successfully published a story today ({today} IST). Skipping this trigger.")
        sys.exit(2)  # workflow checks this exit code to skip remaining steps

    topic_row = get_next_topic()
    if topic_row is None:
        print("⚠️  No pending topics left in topics.csv! Add more rows.")
        sys.exit(1)

    # Printed for the GitHub Actions step to capture as outputs
    print(f"TOPIC::{topic_row['topic']}")
    print(f"GENRE::{topic_row.get('genre', 'mystery')}")

if __name__ == "__main__":
    main()
