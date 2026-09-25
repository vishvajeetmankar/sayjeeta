"""
Step 3: Generate 6-8 distinct scene images (NOT one static image -- see README's YouTube
policy section for why) + a separate dramatic thumbnail, via Pollinations.ai (free, no key).
Output: images/<slug>_scene_01.png ... images/<slug>_scene_NN.png, thumbnails/<slug>.png

ERROR HANDLING:
- Each image retries up to 3x on network/HTTP failure
- If a specific scene image still fails, we reuse the PREVIOUS successful scene's image
  rather than leaving a gap (a repeated frame for one scene is far less noticeable than a
  crash, and keeps the "multiple distinct scenes" requirement mostly intact)
- If the very first scene fails after retries with nothing to fall back on, the whole step
  fails loudly -- there is no video without at least one image
- If the thumbnail generation fails, we fall back to reusing scene 1 as the thumbnail source
  rather than uploading with no thumbnail at all
"""
import os, sys, json, time, requests, urllib.parse

BASE = "https://image.pollinations.ai/prompt/"
STYLE_SUFFIX = ", cinematic lighting, highly detailed, moody atmosphere, 16:9, no text, no watermark"

def fetch_image(prompt: str, out_path: str, width=1920, height=1080, seed=None, retries=3) -> bool:
    encoded = urllib.parse.quote(prompt + STYLE_SUFFIX)
    url = f"{BASE}{encoded}?width={width}&height={height}&nologo=true"
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
            print(f"    retry {attempt}/{retries} failed: {e}")
            time.sleep(3 * attempt)
    return False

def main(slug: str):
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
        ok = fetch_image(scene["image_prompt_english"], out_path, seed=n * 1000 + hash(slug) % 1000)

        if not ok and last_good_path:
            print(f"  ⚠️  Scene {n} failed after retries, reusing previous scene's image instead.")
            out_path = last_good_path
        elif not ok and not last_good_path:
            print(f"❌ Scene {n} (first scene) failed after retries with no fallback available. Aborting.")
            sys.exit(1)
        else:
            last_good_path = out_path

        scene_paths.append({"scene_number": n, "position_pct": scene["position_pct"], "path": out_path})

    with open(f"images/{slug}_scenes.json", "w", encoding="utf-8") as f:
        json.dump(scene_paths, f, ensure_ascii=False, indent=2)

    print("→ Generating thumbnail...")
    thumb_path = f"thumbnails/{slug}.png"
    if not fetch_image(story["thumbnail_prompt_english"], thumb_path, 1280, 720):
        print("  ⚠️  Thumbnail generation failed, falling back to scene 1 as thumbnail source.")
        os.makedirs("thumbnails", exist_ok=True)
        os.system(f'ffmpeg -y -i "{scene_paths[0]["path"]}" -vf scale=1280:720 "{thumb_path}"')

    print(f"✅ {len(scene_paths)} scene images + thumbnail ready")

if __name__ == "__main__":
    main(sys.argv[1])
