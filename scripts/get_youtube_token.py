"""
Run this ONCE, on any computer with a browser (your laptop is fine for this one-time step —
after this, the refresh token lives in GitHub Secrets and everything else runs in the cloud).

1. Go to Google Cloud Console -> create project -> enable "YouTube Data API v3"
2. Create OAuth Client ID, type = "Desktop app" -> download the JSON as client_secret.json
   (place it in this scripts/ folder)
3. Run: python get_youtube_token.py
4. Log in with the Sayjeeta Stories channel's Google account
5. Copy the printed refresh_token into your GitHub Secret YT_REFRESH_TOKEN
"""
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube"]

flow = InstalledAppFlow.from_client_secrets_file("scripts/client_secret.json", SCOPES)
creds = flow.run_local_server(port=0)

print("\n\n=== SAVE THESE AS GITHUB SECRETS ===")
print("YT_CLIENT_ID     =", creds.client_id)
print("YT_CLIENT_SECRET =", creds.client_secret)
print("YT_REFRESH_TOKEN =", creds.refresh_token)
