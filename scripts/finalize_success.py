"""
Runs ONLY after upload_youtube.py has succeeded (the workflow gates this step with
`if: success()`). This is the single place that:
  - marks the processed topic as 'done' in topics.csv
  - records today's IST date as last_success_date in queue/status.json

Keeping this separate from queue_manager.py (which only picks a topic) is the fix for the
bug where a failed run still burned the day's slot and marked a never-generated topic done.
"""
import csv, json, os, sys, datetime
from zoneinfo import ZoneInfo

STATUS_FILE = "queue/status.json"
TOPICS_FILE = "topics.csv"
IST = ZoneInfo("Asia/Kolkata")

def main(topic_text: str):
    with open(TOPICS_FILE, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        fieldnames = rows[0].keys() if rows else ["topic", "genre", "status"]

    found = False
    for row in rows:
        if row["topic"] == topic_text:
            row["status"] = "done"
            found = True

    if not found:
        print(f"⚠️  Topic text not found in topics.csv (may have been edited mid-run): {topic_text[:80]}")
    else:
        with open(TOPICS_FILE, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print("✅ Topic marked done in topics.csv")

    os.makedirs("queue", exist_ok=True)
    status = {}
    if os.path.exists(STATUS_FILE):
        with open(STATUS_FILE, encoding="utf-8") as f:
            status = json.load(f)
    status["last_success_date"] = datetime.datetime.now(IST).date().isoformat()
    status["last_success_topic"] = topic_text
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2, ensure_ascii=False)
    print(f"✅ Recorded success for {status['last_success_date']} (IST)")

if __name__ == "__main__":
    main(sys.argv[1])
