"""
Handles the "2 triggers/day but only 1 story/day" rule.
- Reads topics.csv for the first row with status=pending
- Checks queue/status.json for today's date -> if already processed today, skip (exit code 2)
- On success, marks the row as 'done' and records today's date
"""
import csv, json, os, sys, datetime

STATUS_FILE = "queue/status.json"
TOPICS_FILE = "topics.csv"

def load_status():
    if os.path.exists(STATUS_FILE):
        with open(STATUS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"last_processed_date": None}

def save_status(status):
    os.makedirs("queue", exist_ok=True)
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2)

def get_next_topic():
    with open(TOPICS_FILE, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        if row["status"].strip().lower() == "pending":
            return row, rows
    return None, rows

def mark_done(topic_text, rows):
    with open(TOPICS_FILE, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["topic", "genre", "status"])
        writer.writeheader()
        for row in rows:
            if row["topic"] == topic_text:
                row["status"] = "done"
            writer.writerow(row)

def main():
    today = datetime.date.today().isoformat()
    status = load_status()

    if status.get("last_processed_date") == today:
        print(f"Already processed a story today ({today}). Skipping this trigger.")
        sys.exit(2)  # workflow checks this exit code to skip remaining steps

    topic_row, all_rows = get_next_topic()
    if topic_row is None:
        print("⚠️  No pending topics left in topics.csv! Add more rows.")
        sys.exit(1)

    # Print for the GitHub Actions workflow to capture as outputs
    print(f"TOPIC::{topic_row['topic']}")
    print(f"GENRE::{topic_row['genre']}")

    status["last_processed_date"] = today
    status["current_topic"] = topic_row["topic"]
    save_status(status)

    mark_done(topic_row["topic"], all_rows)

if __name__ == "__main__":
    main()
