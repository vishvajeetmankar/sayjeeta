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

# Two alternating visual styles for scene variety (per user request for "vectors/motion
# graphics" variety instead of every scene looking like the same photoreal-AI-render style).
# Cycles roughly every 3rd scene so most of the video stays cinematic (better for horror/
# mystery mood) with occasional vector-illustration scenes breaking up the visual monotony.
CINEMATIC_SUFFIX = ", cinematic lighting, highly detailed, moody atmosphere, 16:9, no text, no watermark"
VECTOR_SUFFIX = ", flat vector illustration style, bold shapes, limited color palette, clean modern digital art, 16:9, no text, no watermark"

def style_suffix_for_scene(scene_number: int) -> str:
    return VECTOR_SUFFIX if scene_number % 3 == 0 else CINEMATIC_SUFFIX

def fetch_image(prompt: str, out_path: str, style_suffix: str, width=1920, height=1080, seed=None, retries=3) -> bool:
    encoded = urllib.parse.quote(prompt + style_suffix)
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
        style = style_suffix_for_scene(n)
        print(f"→ Scene {n}/{len(scenes)} [{'vector' if style is VECTOR_SUFFIX else 'cinematic'}]: {scene['image_prompt_english'][:60]}...")
        ok = fetch_image(scene["image_prompt_english"], out_path, style, seed=n * 1000 + hash(slug) % 1000)

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
    # Thumbnail always stays cinematic/photoreal-leaning -- that's what drives clicks; the
    # vector-style variety is for breaking up mid-video monotony, not for the CTR-critical thumb.
    if not fetch_image(story["thumbnail_prompt_english"], thumb_path, CINEMATIC_SUFFIX, 1280, 720):
        print("  ⚠️  Thumbnail generation failed, falling back to scene 1 as thumbnail source.")
        os.makedirs("thumbnails", exist_ok=True)
        os.system(f'ffmpeg -y -i "{scene_paths[0]["path"]}" -vf scale=1280:720 "{thumb_path}"')

    print(f"✅ {len(scene_paths)} scene images + thumbnail ready")

if __name__ == "__main__":
    main(sys.argv[1])
