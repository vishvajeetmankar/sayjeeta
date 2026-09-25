"""
Step 6: Upload as PRIVATE + scheduled publishAt (1-day buffer from today).
This is the automatable part. Premiere-mode toggle and End Screen are NOT exposed by
YouTube's API (Studio-only) -- that's the daily 2-minute manual step described in README.md.

ERROR HANDLING:
- Refresh-token failure is caught SPECIFICALLY and given a clear, actionable message: this is
  the single most common failure mode for a long-running unattended uploader (token revoked,
  password changed, or the OAuth app was never switched to "Production" -- see README) and a
  generic stack trace would just confuse future-you at 6 AM.
- The resumable upload loop retries transient chunk failures (network blips, 5xx) up to 5x
  with backoff before giving up, instead of dying on the first hiccup of a 15-20 min file.
- Thumbnail upload and the post-upload verification call are each independently wrapped: if
  either fails, the video (already uploaded and scheduled) is NOT rolled back or deleted --
  losing the thumbnail is cosmetic and fixable by hand, re-uploading the whole video is not
  something you want triggered by a thumbnail hiccup.
"""
import os, sys, json, time, datetime
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube"]

# Set to True only if your images/voice could be mistaken for a REAL person/event (they
# shouldn't be, for fictional stylized story content) -- see README's policy section.
CONTAINS_SYNTHETIC_MEDIA = os.environ.get("CONTAINS_SYNTHETIC_MEDIA", "false").lower() == "true"

def get_youtube_client():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        scopes=SCOPES,
    )
    try:
        creds.refresh(Request())
    except RefreshError as e:
        print("❌ YOUTUBE REFRESH TOKEN IS INVALID/EXPIRED.")
        print("   Most likely cause: the Google Cloud OAuth consent screen is still in")
        print("   'Testing' mode (tokens auto-expire after 7 days), or the token was revoked.")
        print("   Fix: go to Google Cloud Console -> OAuth consent screen -> Audience ->")
        print("   click 'Publish App', then re-run scripts/get_youtube_token.py to get a")
        print("   fresh, non-expiring refresh token. Full steps are in README.md.")
        print(f"   Raw error: {e}")
        sys.exit(1)
    return build("youtube", "v3", credentials=creds)

def next_publish_slot(hour_utc=14):
    """Buffers 1 day ahead so there's always a finished video queued before it goes live."""
    now = datetime.datetime.utcnow()
    target = (now + datetime.timedelta(days=1)).replace(
        hour=hour_utc, minute=0, second=0, microsecond=0
    )
    return target.strftime("%Y-%m-%dT%H:%M:%SZ")

def upload_with_retry(request, max_retries=5):
    response = None
    retry_count = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                print(f"  {int(status.progress() * 100)}% uploaded")
        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504) and retry_count < max_retries:
                retry_count += 1
                wait = 2 ** retry_count
                print(f"  Transient upload error ({e.resp.status}), retry {retry_count}/{max_retries} in {wait}s...")
                time.sleep(wait)
                continue
            raise
    return response

def main(slug: str):
    with open(f"stories/{slug}.json", encoding="utf-8") as f:
        story = json.load(f)

    video_path = f"videos/{slug}.mp4"
    if not os.path.exists(video_path):
        print(f"❌ No video found at {video_path} -- nothing to upload.")
        sys.exit(1)

    youtube = get_youtube_client()

    description = story["description_hindi"] + "\n\n" + " ".join(
        f"#{h}" for h in story.get("hashtags_english", [])
    )

    body = {
        "snippet": {
            "title": story["title_hindi"][:100],
            "description": description,
            "tags": story.get("tags_english", []),
            "categoryId": story.get("category_id", "24"),
        },
        "status": {
            "privacyStatus": "private",
            "publishAt": next_publish_slot(),
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": CONTAINS_SYNTHETIC_MEDIA,
        },
    }

    media = MediaFileUpload(video_path, chunksize=1024 * 1024, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    print("→ Uploading to YouTube...")
    try:
        response = upload_with_retry(request)
    except HttpError as e:
        print(f"❌ Upload failed permanently: {e}")
        if e.resp.status == 403:
            print("   A 403 here usually means the YouTube Data API quota is exhausted for today,")
            print("   or the channel isn't linked correctly to this OAuth app. Check the API")
            print("   quota dashboard in Google Cloud Console.")
        sys.exit(1)

    video_id = response["id"]
    print(f"✅ Uploaded: https://youtube.com/watch?v={video_id}")

    # Thumbnail: independent failure, doesn't roll back the upload
    try:
        youtube.thumbnails().set(
            videoId=video_id, media_body=MediaFileUpload(f"thumbnails/{slug}.png")
        ).execute()
        print("✅ Thumbnail set")
    except Exception as e:
        print(f"⚠️  Thumbnail upload failed ({e}) -- video is still live/scheduled, "
              f"set the thumbnail manually in Studio.")

    # Verification: independent failure, just skip the confirmation print
    try:
        check = youtube.videos().list(part="status", id=video_id).execute()
        st = check["items"][0]["status"]
        print(f"→ Verify: privacyStatus={st['privacyStatus']}, publishAt={st.get('publishAt')}")
    except Exception as e:
        print(f"⚠️  Could not verify upload status ({e}), but the video was uploaded -- check Studio.")

    print("⚠️  Reminder: go set 'Premiere' + End Screen on this video in YouTube Studio (2 min).")
    return video_id

if __name__ == "__main__":
    main(sys.argv[1])
