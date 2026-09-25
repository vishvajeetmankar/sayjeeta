# 🎧 Sayjeeta Stories —

## 1. One-time setup — full walkthrough

### A. Sarvam AI (free Hindi-native story LLM)

1. Go to https://www.sarvam.ai → sign up → open the dashboard
2. Generate an API key (chat completions are free per token as of writing)
3. Save it somewhere — this becomes the `SARVAM_API_KEY` secret

### B. OpenRouter (free hook/polish pass)

1. Go to https://openrouter.ai → sign up → **Keys** → create a new key
2. Note which free models are currently live on your account (they rotate) — open
   `scripts/generate_story.py` and check the `model` field matches one that's active
3. Save the key — this becomes `OPENROUTER_API_KEY`

### C. Google Cloud project + YouTube Data API

1. Go to https://console.cloud.google.com → create a new project (any name)
2. Left menu → **APIs & Services** → **Library** → search "YouTube Data API v3" → **Enable**
3. Left menu → **APIs & Services** → **OAuth consent screen**
   - User type: **External**
   - Fill app name (e.g. "Sayjeeta Uploader"), your email for support + developer contact
   - Scopes: add `.../auth/youtube.upload` and `.../auth/youtube`
   - Test users: add your own Gmail (the one that owns/manages the Sayjeeta Stories channel)
4. Left menu → **Credentials** → **Create Credentials** → **OAuth client ID** → type
   **Desktop app** → download the JSON, rename it `client_secret.json`, place it in this
   project's `scripts/` folder (never commit this file — it's already in `.gitignore`)

### D. ⚠️ Publish the OAuth app to Production (the step you mentioned doing yourself)

This is the fix for the 7-day token expiry:

1. Google Cloud Console → **APIs & Services** → **OAuth consent screen** (or under the newer
   **Google Auth Platform → Audience** menu, depending on when you're reading this)
2. Find **Publishing status: Testing**
3. Click **Publish App** (sometimes labeled "Move to production") → confirm
4. Status should now read **In production**

You'll see a **"This app isn't verified"** warning when you log in afterward — that's
expected and fine for a single-user personal automation like this (verification is a
multi-week Google review process meant for public-facing apps with many users; it isn't
required to just stop your own token from expiring). Click "Advanced" → "Go to [app name]
(unsafe)" → continue. This is you authorizing your own app, not a third party.

### E. Generate the refresh token

Run this once, on any computer with a browser (does not need to be a good PC — this is the
only local step, and only once, ever):

```bash
pip install google-auth-oauthlib google-api-python-client
python scripts/get_youtube_token.py
```

Log in with the Sayjeeta Stories channel's Google account. It prints:

```
YT_CLIENT_ID     = ...
YT_CLIENT_SECRET = ...
YT_REFRESH_TOKEN = ...
```

**Do this AFTER Step D (publishing to Production)**, not before — a token generated while
still in Testing mode will still expire in 7 days even if you publish the app afterward.
If you already generated one before publishing, just delete it and re-run this step.

### F. GitHub repo + Secrets

1. Create a new GitHub repo (public is fine — unlimited free Actions minutes; private repos
   get 2,000 free minutes/month, still comfortably enough for daily runs)
2. Push everything in this folder to that repo
3. Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**,
   add each of:
   ```
   SARVAM_API_KEY
   OPENROUTER_API_KEY
   YT_CLIENT_ID
   YT_CLIENT_SECRET
   YT_REFRESH_TOKEN
   ```
4. Optional (recommended): `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` — if set, you get pinged
   the moment a daily run fails instead of finding out days later. Skip these and the workflow
   just silently doesn't send notifications.

### G. Background music

Create genre subfolders and drop royalty-free MP3s from **YouTube Audio Library** (inside
YouTube Studio → Audio Library) or **Pixabay Music** (both explicitly free for monetized use):

```
music/horror/*.mp3
music/mystery/*.mp3
music/thriller/*.mp3
music/emotional/*.mp3
music/motivational/*.mp3
```

### H. Add your topics

Edit `topics.csv`, add as many rows as you want — you can front-load hundreds at once:

```csv
topic,genre,status
"Ek aadmi ko har raat 3:33 AM par apne future se phone aata hai.",horror,pending
```

### I. Test it before trusting the schedule

Repo → **Actions** tab → select "Daily Audiobook Pipeline" → **Run workflow** (manual
trigger button) → watch it run. Fix anything that errors (see Section 4) before letting the
cron schedule run unattended. This one manual test saves you from a surprise failure at
6:30 AM with nobody watching.

---

## 2. Daily workflow — what YOU actually do

Everything below Step I above is automatic. Your only recurring task:

**Once a day, ~2 minutes:** open YouTube Studio → find the newly-uploaded private/scheduled
video → toggle **"Set as Premiere"** (Content tab → Visibility → this option appears for
private/scheduled videos) and apply your **End Screen template** (save one once as a
reusable template, then it's a couple of clicks per video, not a rebuild each time). Neither
of these is settable via the API — that's a Studio-only limitation on YouTube's side, not a
gap in this project.

---

## 3. Expected errors & how they're handled

Every script now has real error handling instead of crashing on the first hiccup. Here's
what happens when things go wrong, so a 6 AM failure doesn't leave you guessing:

| Where | What can fail | What the system does |
|---|---|---|
| `generate_story.py` | Sarvam API down/rate-limited | Retries 3x with backoff, then **hard-fails the run** (no story = nothing downstream can run) |
| `generate_story.py` | OpenRouter polish fails or free model got deprecated | Falls back to the unpolished Sarvam draft — degraded quality, not a blocked pipeline |
| `generate_story.py` | Model returns malformed JSON or too few scenes | Fails loudly with the exact validation error, rather than passing broken data downstream |
| `generate_voice.py` | Edge-TTS drops connection on a line | Retries 3x per segment; if it still fails, that one line is skipped and logged, not a crashed video — unless >15% of all segments fail, which stops the run (something systemic, worth checking manually) |
| `generate_image.py` | Pollinations times out or returns junk | Retries 3x; if a scene still fails, reuses the previous scene's image instead of leaving a gap; only the very first scene failing with nothing to fall back on stops the run |
| `generate_captions.py` | Whisper model fails to load / transcription fails | Marked **non-critical** in the workflow — video uploads without captions rather than not uploading at all |
| `make_video.py` | One scene clip fails to render | That scene's time is merged into the next scene instead of crashing the whole render |
| `make_video.py` | Final ffmpeg render fails or produces a corrupt file | The broken file is deleted so it can never get uploaded by accident |
| `upload_youtube.py` | Refresh token expired/revoked | Fails with an exact, actionable message pointing at Section 1.D above — this is the #1 most common failure in any long-running unattended uploader |
| `upload_youtube.py` | Transient 5xx during upload | Retries up to 5x with backoff before giving up |
| `upload_youtube.py` | Thumbnail set fails | Video stays uploaded/scheduled; you just set the thumbnail manually that one time |
| Any step | Repeated failure | Debug logs (story JSON, failed-segment list) are attached as a downloadable GitHub Actions artifact on the failed run, and a Telegram ping fires if you set up Section 1.F's optional secrets |

**Quota note:** YouTube Data API's default daily quota is 10,000 units; one video upload
costs ~1,600 units. At 1 upload/day you'll never come close to the limit.

---

## 4. YouTube policy compliance — what changed and why

Read this before you assume "more automation = better." YouTube's monetization rules
sharpened considerably through 2025-2026, specifically targeting the exact shape of channel
this project could easily become if built carelessly:

- **"Inauthentic content" policy** (renamed from "repetitious content" in mid-2025) blocks
  monetization for mass-produced, templated uploads — explicitly including *"readings of
  content over static images/slideshows."* This is why the pipeline generates **6-8 distinct
  scene images** instead of one image on a zoom loop, and why the story prompt forces
  visual variety scene-by-scene.
- **AI disclosure**: every generated description includes a plain-language line stating the
  story was AI-assisted with human editorial direction. This isn't the same as the API's
  `containsSyntheticMedia` flag (that one is specifically for realistic/deepfake-style
  content depicting real people, places or events — a stylized fictional horror-story
  illustration normally doesn't need it, which is why it defaults to `false` in
  `upload_youtube.py`). Keep your generated images stylized/illustrative rather than
  photorealistic-of-real-places as an extra safety margin.
- **What YouTube actually rewards**, per their own Creator Liaison's public statements: it's
  not about whether AI was used, it's whether a human is exercising real editorial
  judgment — topic selection, quality control, original perspective. That's exactly why the
  daily 2-minute manual step (Section 2) and your own curation of `topics.csv` matter more
  than they might seem to — they're the "human in the loop" signal that keeps this channel
  on the right side of the policy, not just a nice-to-have.
- **Practical advice**: don't set this to blindly upload 5-10 videos/day even though the
  quota technically allows it. One a day, with a human reviewing the output before it goes
  public (you'll see it as "private" for a full day before it's scheduled to publish —
  use that window to skim it), is both policy-safer and matches what's actually working for
  real Hindi story channels (see below).

---

## 5. What's actually working for Hindi audio-story channels right now (research findings)

A few consistent patterns showed up across current top Hindi horror/mystery/story channels
and podcasts, and the pipeline is built to reflect them rather than fight them:

- **Oral-tradition framing wins.** The "dadi-nani ki kahani" / chaupal storytelling lineage
  is a genuine cultural throughline in what's succeeding — not generic "AI story" framing.
  The story prompt (`prompts/story_prompt.txt`) explicitly writes in this voice.
- **"Sach ghatna jaisi lagti hai" (feels like a true event) framing** consistently
  outperforms obviously-fictional framing for horror/mystery, without literally claiming
  false facts — it's a narrative device ("aap khud faisla kariye"), not a factual claim.
- **The voice performs, it doesn't read.** Every serious competitor in this space (VoisLabs,
  professional narrators like Neelesh Misra's work) treats emotional delivery — pacing,
  whispers, dramatic pauses — as the actual product. That's why `generate_voice.py` now
  parses `[DRAMATIC]`/`[WHISPER]`/`[EXCITED]`/`[SAD]` tags into real rate/pitch changes per
  line instead of one flat tone for 20 minutes.
- **Daily consistency + episode numbering builds return audience** more than any single
  video's virality. The story prompt asks for "Ep. N -" style titling for this reason.
  Consider a consistent host persona/intro line too (even a single pre-recorded 5-second
  "Sayjeeta Stories mein aapka swagat hai" jingle in your own voice, reused every episode,
  adds a strong authenticity + branding signal that pure-AI channels lack).
- **Captions are genuinely part of engagement**, not just accessibility — this matches what
  you said, and the karaoke-style word-pop captions here follow the same visual pattern used
  across viral short-form and long-form content globally.
- **Where these channels earn**: horror/mystery audio-story channels in this niche have been
  reported around ₹200-500+ CPM-equivalent ranges depending on watch time and audience
  geography — respectable but not overnight-riches; consistency over months is what compounds.

None of this is a virality guarantee (see the note at the very top) — it's what reduces the
gap between "technically works" and "actually competitive" as much as research can tell you.

---

## 6. Folder map

```
AI_YT_FACTORY/
├── topics.csv              ← YOU maintain this
├── queue/status.json       ← tracks pending/done + prevents double-processing same day
├── .github/workflows/      ← the automation cron
├── scripts/                ← all pipeline code (see Section 3 for error handling per script)
├── prompts/                ← story + emotion-tag + multi-scene prompt template
├── music/<genre>/          ← YOU drop royalty-free BGM here
├── stories/ voices/ images/ captions/ videos/ thumbnails/  ← auto-generated, gitignored
```
