# scripts/generate_story.py

```python
"""
Step 1: Generate the Hindi story.
- Draft pass: Sarvam-105B (free tier, Hindi-native grammar, v1 endpoint)
- Polish pass: OpenRouter free model (sharpens hook + retention beats)
Output: stories/<slug>.json

ERROR HANDLING:
- Both API calls retry up to 3x with exponential backoff (handles rate limits / transient 5xx)
- Output JSON is validated for required keys + minimum scene count; invalid output raises a
  clear error instead of silently producing a broken pipeline downstream
- If Sarvam fails after retries -> hard fail (no story = nothing else can run, workflow should stop)
- If OpenRouter polish fails after retries -> falls back to the unpolished draft (degraded, not blocked)
"""
import os, json, re, sys, time, requests

SARVAM_API_KEY = os.environ["SARVAM_API_KEY"]
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]

# NOTE ON ENDPOINT/MODEL: v2/chat/completions returned "this endpoint is currently in beta
# and not available" for this account (beta access is opt-in per Sarvam account, not granted
# by default). Switched to v1/chat/completions, which is generally available and serves
# sarvam-105b / sarvam-105b-conversations. If you later get v2 beta access and want the
# cheaper/faster sarvam-30b, change both SARVAM_URL and SARVAM_MODEL together.
SARVAM_URL = "https://api.sarvam.ai/v1/chat/completions"
SARVAM_MODEL = "sarvam-105b"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

REQUIRED_KEYS = ["title_hindi", "hook_line", "story_script", "description_hindi",
                  "scenes", "thumbnail_prompt_english", "tags_english", "hashtags_english"]

class StoryGenError(Exception):
    pass

def _post_with_retry(url, headers, payload, label, max_retries=3):
    """
    Retries only TRANSIENT failures (timeouts, connection errors, 429, 5xx).
    A 4xx (400/401/403/404) means the request itself is wrong -- retrying it verbatim
    3 times just wastes time and hides the real problem, so those fail immediately with
    the response body printed (that body is where the actual "why" lives).
    """
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=180)
            if resp.status_code == 429:
                wait = 2 ** attempt
                print(f"  [{label}] rate limited (429), waiting {wait}s...")
                time.sleep(wait)
                continue
            if 400 <= resp.status_code < 500:
                print(f"  [{label}] {resp.status_code} response body: {resp.text[:1000]}")
                raise StoryGenError(f"{label} rejected the request ({resp.status_code}): {resp.text[:500]}")
            resp.raise_for_status()  # covers 5xx -> falls into the except block below and retries
            return resp.json()["choices"][0]["message"]["content"]
        except StoryGenError:
            raise  # 4xx: don't retry, don't swallow -- surface immediately
        except requests.exceptions.RequestException as e:
            last_err = e
            wait = 2 ** attempt
            print(f"  [{label}] attempt {attempt}/{max_retries} failed: {e}. Retrying in {wait}s...")
            time.sleep(wait)
    raise StoryGenError(f"{label} failed after {max_retries} retries: {last_err}")

def call_sarvam(prompt: str) -> str:
    return _post_with_retry(
        SARVAM_URL,
        {"api-subscription-key": SARVAM_API_KEY, "Content-Type": "application/json"},
        {"model": SARVAM_MODEL, "messages": [{"role": "user", "content": prompt}],
         "temperature": 0.85, "max_tokens": 6000},
        "Sarvam",
    )

def call_openrouter_polish(draft_json_str: str, genre: str) -> str:
    polish_instruction = f"""Tumhe ek Hindi audiobook story ka JSON diya gaya hai. Structure mat
badlo, sirf 3 cheezein improve karo:
1. Pehli 3 lines ko aur zyada scroll-stopping / hook-heavy banao.
2. "story_script" mein jahan pacing slow lage, waha ek chhota retention-hook daalo. Emotion
   tags ([NORMAL]/[DRAMATIC]/[WHISPER]/[EXCITED]/[SAD]) aur speaker tags waise hi rakho.
3. "title_hindi" ko aur zyada curiosity-gap wala banao (bina clickbait jhoot ke).

Genre: {genre}
Baaki sab same JSON structure mein wapas do (scenes array bhi as-is rakho), sirf upar wale
improvements ke saath. Sirf valid JSON return karo:

{draft_json_str}
"""
    return _post_with_retry(
        OPENROUTER_URL,
        {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
        {"model": "qwen/qwen-2.5-72b-instruct:free",  # swap for whichever free model is live
         "messages": [{"role": "user", "content": polish_instruction}],
         "temperature": 0.7, "max_tokens": 6000},
        "OpenRouter",
    )

def extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise StoryGenError(f"No JSON object found in model output. Raw output (first 500 chars):\n{text[:500]}")
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as e:
        raise StoryGenError(f"Model output wasn't valid JSON ({e}). Raw (first 500 chars):\n{text[:500]}")

def validate_story(data: dict):
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        raise StoryGenError(f"Story JSON is missing required keys: {missing}")
    if not isinstance(data["scenes"], list) or len(data["scenes"]) < 4:
        raise StoryGenError(f"Expected at least 4 scenes, got {len(data.get('scenes', []))}. "
                             f"Single/few-image stories risk YouTube's inauthentic-content policy.")
    if len(data["story_script"]) < 500:
        raise StoryGenError("story_script looks too short (<500 chars) — likely a truncated/failed generation.")

def slugify(title: str) -> str:
    s = re.sub(r"[^\w\s-]", "", title, flags=re.UNICODE).strip().lower()
    s = re.sub(r"[\s]+", "-", s)
    return s[:60] or "story"

def main(topic: str, genre: str):
    with open("prompts/story_prompt.txt", encoding="utf-8") as f:
        template = f.read()
    prompt = template.format(topic=topic, genre=genre)

    print(f"→ Draft pass ({SARVAM_MODEL})...")
    draft_raw = call_sarvam(prompt)          # hard-fails the whole run if this errors -- correct,
    draft = extract_json(draft_raw)          # there's no usable story without it
    validate_story(draft)

    print("→ Polish pass (OpenRouter)...")
    final = draft
    try:
        polished_raw = call_openrouter_polish(json.dumps(draft, ensure_ascii=False), genre)
        polished = extract_json(polished_raw)
        validate_story(polished)
        final = polished
    except StoryGenError as e:
        print(f"⚠️  Polish pass failed or returned invalid data ({e}). Falling back to unpolished draft.")

    slug = slugify(final.get("title_hindi", "story"))
    os.makedirs("stories", exist_ok=True)
    out_path = f"stories/{slug}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False, indent=2)

    print(f"✅ Story saved: {out_path} ({len(final['scenes'])} scenes)")
    print(f"SLUG::{slug}")  # explicit marker, grepped by the workflow -- never confused with a log/error line

if __name__ == "__main__":
    topic_arg = sys.argv[1]
    genre_arg = sys.argv[2] if len(sys.argv) > 2 else "mystery"
    try:
        main(topic_arg, genre_arg)
    except StoryGenError as e:
        print(f"❌ STORY GENERATION FAILED: {e}")
        sys.exit(1)
```
