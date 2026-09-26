"""
Step 3: Generate 10-14 distinct scene images (NOT one static image, and NOT all from one
source -- see README's YouTube policy section on why scene variety matters) + a separate
dramatic thumbnail.

Three sources rotate across scenes for visual variety:
  - Pollinations.ai (free, no key) -- AI-generated cinematic renders, from image_prompt_english
  - Pixabay photos (free API key) -- real stock photography, from stock_keywords_english
  - Pixabay vectors (free API key) -- flat vector illustrations, from stock_keywords_english
Rotation: scene_number % 3 == 1 -> Pollinations, == 2 -> Pixabay photo, == 0 -> Pixabay vector.
This is what actually delivers "vectors + more visual variety" rather than every scene being
the same AI-render style.

PIXABAY_API_KEY is optional: if it's not set, every scene just falls back to Pollinations
(cinematic) and the run still works -- Pixabay is a variety upgrade, not a hard dependency.

Output: images/<slug>_scene_01.png ... images/<slug>_scene_NN.png, thumbnails/<slug>.png

ERROR HANDLING:
- Each Pollinations fetch retries up to 3x on network/HTTP failure
- Each Pixabay search retries up to 2x on network/HTTP failure; if it still fails, or if the
  search genuinely returns zero results for that scene's keywords (real search, not
  generation -- this WILL happen for obscure/fictional keywords), that scene falls back to
  Pollinations using the full cinematic prompt instead, rather than leaving a gap
- If a scene still has no usable image after its fallback, we reuse the PREVIOUS successful
  scene's image rather than leaving a gap entirely
- If the very first scene fails after every fallback with nothing to reuse, the whole step
  fails loudly -- there is no video without at least one image
- If thumbnail generation fails, falls back to reusing scene 1 as the thumbnail source
"""
import os, sys, json, time, random, requests, urllib.parse

POLLINATIONS_BASE = "https://image.pollinations.ai/prompt/"
PIXABAY_BASE = "https://pixabay.com/api/"
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY", "").strip()

CINEMATIC_SUFFIX = ", cinematic lighting, highly detailed, moody atmosphere, 16:9, no text, no watermark"

def fetch_pollinations(prompt: str, out_path: str, width=1920, height=1080, seed=None, retries=3) -> bool:
    encoded = urllib.parse.quote(prompt + CINEMATIC_SUFFIX)
    url = f"{POLLINATIONS_BASE}{encoded}?width={width}&height={height}&nologo=true"
    if seed:
        url += f"&seed={seed}"
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, timeout=120)
            r.raise_for_status()
            if len(r.content) < 5000:  # tiny/error image, not a real render
                raise IOError("Response too small to be a real image")
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "wb") as f:
                f.write(r.content)
            return True
        except Exception as e:
            print(f"    [pollinations] retry {attempt}/{retries} failed: {e}")
            time.sleep(3 * attempt)
    return False

def fetch_pixabay(query: str, image_type: str, out_path: str, retries=2) -> bool:
    """image_type: 'photo' or 'vector'. Returns False on any failure OR zero results --
    caller is expected to fall back to Pollinations in both cases."""
    if not PIXABAY_API_KEY:
        return False
    params = {
        "key": PIXABAY_API_KEY,
        "q": query,
        "image_type": image_type,
        "orientation": "horizontal",
        "safesearch": "true",
        "per_page": 20,
    }
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(PIXABAY_BASE, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            hits = data.get("hits", [])
            if not hits:
                print(f"    [pixabay/{image_type}] zero results for '{query}' -- falling back to Pollinations")
                return False
            # Pick randomly among the top results rather than always the first -- avoids the
            # same handful of stock images recurring across every episode that uses similar
            # keywords (e.g. "old haveli night" will come up a lot for this channel's genre).
            choice = random.choice(hits[: min(10, len(hits))])
            image_url = choice.get("largeImageURL") or choice.get("webformatURL")
            if not image_url:
                return False
            img_resp = requests.get(image_url, timeout=60)
            img_resp.raise_for_status()
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "wb") as f:
                f.write(img_resp.content)
            return True
        except Exception as e:
            print(f"    [pixabay/{image_type}] retry {attempt}/{retries} failed: {e}")
            time.sleep(2 * attempt)
    return False

def get_scene_image(scene: dict, out_path: str, seed: int) -> bool:
    n = scene["scene_number"]
    prompt = scene["image_prompt_english"]
    keywords = scene.get("stock_keywords_english", "").strip()
    slot = n % 3

    if slot == 1 or not keywords:
        print(f"  source: pollinations (cinematic)")
        return fetch_pollinations(prompt, out_path, seed=seed)
    elif slot == 2:
        print(f"  source: pixabay (photo) -- '{keywords}'")
        if fetch_pixabay(keywords, "photo", out_path):
            return True
        print(f"  falling back to pollinations for scene {n}")
        return fetch_pollinations(prompt, out_path, seed=seed)
    else:  # slot == 0
        print(f"  source: pixabay (vector) -- '{keywords}'")
        if fetch_pixabay(keywords, "vector", out_path):
            return True
        print(f"  falling back to pollinations for scene {n}")
        return fetch_pollinations(prompt, out_path, seed=seed)

def main(slug: str):
    if not PIXABAY_API_KEY:
        print("⚠️  PIXABAY_API_KEY not set -- all scenes will use Pollinations only "
              "(still works, just less visual variety). Add the secret to enable Pixabay.")

    with open(f"stories/{slug}.json", encoding="utf-8") as f:
        story = json.load(f)

    scenes = story["scenes"]
    os.makedirs("images", exist_ok=True)
    last_good_path = None
    scene_paths = []

    for scene in scenes:
        n = scene["scene_number"]
        out_path = f"images/{slug}_scene_{n:02d}.png"
        print(f"→ Scene {n}/{len(scenes)}: {scene['image_prompt_english'][:60]}...")
        ok = get_scene_image(scene, out_path, seed=n * 1000 + hash(slug) % 1000)

        if not ok and last_good_path:
            print(f"  ⚠️  Scene {n} failed on every source, reusing previous scene's image instead.")
            out_path = last_good_path
        elif not ok and not last_good_path:
            print(f"❌ Scene {n} (first scene) failed on every source with no fallback available. Aborting.")
            sys.exit(1)
        else:
            last_good_path = out_path

        scene_paths.append({"scene_number": n, "position_pct": scene["position_pct"], "path": out_path})

    with open(f"images/{slug}_scenes.json", "w", encoding="utf-8") as f:
        json.dump(scene_paths, f, ensure_ascii=False, indent=2)

    print("→ Generating thumbnail...")
    thumb_path = f"thumbnails/{slug}.png"
    # Thumbnail always stays Pollinations cinematic -- that's what drives clicks; the stock
    # photo/vector variety is for breaking up mid-video monotony, not the CTR-critical thumb.
    if not fetch_pollinations(story["thumbnail_prompt_english"], thumb_path, 1280, 720):
        print("  ⚠️  Thumbnail generation failed, falling back to scene 1 as thumbnail source.")
        os.makedirs("thumbnails", exist_ok=True)
        os.system(f'ffmpeg -y -i "{scene_paths[0]["path"]}" -vf scale=1280:720 "{thumb_path}"')

    print(f"✅ {len(scene_paths)} scene images + thumbnail ready")

if __name__ == "__main__":
    main(sys.argv[1])
