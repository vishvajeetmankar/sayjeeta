"""
Step 4: Word-level captions in a 3-line "teleprompter" scroll style.

Fixes two complaints with the old single-line captions:
1. Captions were too large and each line just flashed on/off with nothing to anchor the eye.
2. There was no sense of continuous flow.

New layout, for every chunk of 3-5 words (random per chunk, and cut short immediately at a
sentence-ending punctuation mark even if the target count isn't reached yet -- see
chunk_words()):
  - CENTER slot: the chunk currently being spoken -- full size, full opacity, karaoke
    word-highlight (the active word pops in yellow, same as before).
  - ABOVE slot: the PREVIOUS chunk (just finished) -- smaller, lower opacity, faded in/out,
    positioned above center. Once we move to the next chunk, this slot's content changes to
    what was center a moment ago, and the chunk before THAT disappears -- giving the
    continuous "scrolling up and fading" feel that was asked for, without needing true
    per-frame motion interpolation (which .ass doesn't support well for text reflow anyway).
  - BELOW slot: the NEXT chunk (coming up) -- smaller, lower opacity, a preview.

faster-whisper transcribes the final voice audio with word timestamps; pysubs2 builds the
styled .ass file with three overlapping event layers per chunk window.

ERROR HANDLING:
- Non-critical step (see workflow yaml, continue-on-error: true): if transcription fails or
  produces zero words, this exits cleanly with a warning and writes no .ass file --
  make_video.py already checks for that file's existence and renders caption-free if missing.
- Model size falls back automatically: tries 'small' first, falls back to 'base' if that
  fails to load (e.g. low memory on the free GitHub runner).
Output: captions/<slug>.ass
"""
import sys, os, json, random
from faster_whisper import WhisperModel
import pysubs2

MIN_WORDS_PER_LINE = 3
MAX_WORDS_PER_LINE = 5
SENTENCE_END_CHARS = (".", "?", "!", "।", "…")

# Noto Sans is what's actually installed on the GitHub runner (via `apt-get install fonts-noto`
# in the workflow) and, critically, has full Devanagari glyph coverage -- the previous
# "Poppins ExtraBold" was never installed at all and has no Hindi glyphs regardless, so
# captions were silently falling back to whatever fontconfig substituted. Naming the real,
# available, Devanagari-capable font explicitly avoids relying on that accident.
FONT_NAME = "Noto Sans"

def build_styles(subs: pysubs2.SSAFile):
    center = pysubs2.SSAStyle()
    center.fontname = FONT_NAME
    center.fontsize = 68  # raised from 58 -- was reported as too small in the 3-line layout
    center.bold = True
    center.primarycolor = pysubs2.Color(255, 255, 255)
    center.outlinecolor = pysubs2.Color(0, 0, 0)
    center.outline = 4
    center.shadow = 0
    center.alignment = 2  # bottom-center anchor; we offset via marginv per-style
    center.marginv = 160
    subs.styles["Center"] = center

    context = pysubs2.SSAStyle()
    context.fontname = FONT_NAME
    context.fontsize = 42  # raised from 34
    context.bold = True
    context.primarycolor = pysubs2.Color(255, 255, 255)
    context.outlinecolor = pysubs2.Color(0, 0, 0)
    context.outline = 2
    context.shadow = 0
    context.alignment = 2
    subs.styles["Above"] = context
    subs.styles["Below"] = context.copy()

def chunk_words(words: list) -> list:
    """
    Groups words into lines of a RANDOM 3-5 word target (not a fixed count), and cuts a line
    short immediately whenever a word ends in sentence-final punctuation -- even if the random
    target hasn't been reached yet -- so a caption line never straddles a sentence boundary.
    """
    chunks, current = [], []
    target = random.randint(MIN_WORDS_PER_LINE, MAX_WORDS_PER_LINE)
    for w in words:
        current.append(w)
        ends_sentence = w["word"].strip().endswith(SENTENCE_END_CHARS)
        if ends_sentence or len(current) >= target:
            chunks.append(current)
            current = []
            target = random.randint(MIN_WORDS_PER_LINE, MAX_WORDS_PER_LINE)
    if current:
        chunks.append(current)
    return chunks

def make_karaoke_text(words: list) -> str:
    parts = []
    for w in words:
        dur_cs = max(1, int((w["end"] - w["start"]) * 100))
        parts.append(rf"{{\k{dur_cs}\c&H00FFFF&}}{w['word']} {{\c&HFFFFFF&}}")
    return "".join(parts)

def plain_text(words: list) -> str:
    return " ".join(w["word"] for w in words)

def load_model():
    for size in ("small", "base"):
        try:
            return WhisperModel(size, device="cpu", compute_type="int8")
        except Exception as e:
            print(f"  Could not load Whisper '{size}' model ({e}), trying a smaller one...")
    return None

def main(slug: str):
    audio_path = f"voices/{slug}.mp3"
    print("→ Transcribing with faster-whisper...")

    try:
        model = load_model()
        if model is None:
            print("⚠️  No Whisper model could be loaded -- skipping captions for this video.")
            return
        segments, _ = model.transcribe(audio_path, language="hi", word_timestamps=True)
        words = []
        for seg in segments:
            for w in seg.words:
                words.append({"word": w.word.strip(), "start": w.start, "end": w.end})
    except Exception as e:
        print(f"⚠️  Transcription failed ({e}) -- skipping captions for this video, not failing the run.")
        return

    if not words:
        print("⚠️  Transcription produced zero words -- skipping captions for this video.")
        return

    # Group into variable-length chunks (3-5 words, cut early at sentence boundaries) -- each
    # chunk = one "line" that will occupy center/above/below
    chunks = chunk_words(words)

    subs = pysubs2.SSAFile()
    build_styles(subs)

    # MUST match make_video.py's zoompan output resolution (1920x1080) -- without this,
    # libass doesn't know the script's coordinate space matches the video's, and every
    # \pos() below would land in the wrong place (this bit me while writing this: pysubs2
    # doesn't default PlayResX/Y to anything sensible on its own).
    subs.info["PlayResX"] = "1920"
    subs.info["PlayResY"] = "1080"

    CENTER_Y = 1080 - 160        # matches the "Center" style's alignment=2 + marginv=160
    ABOVE_Y = CENTER_Y - 100     # smaller y = higher on screen = the line that just finished
    BELOW_Y = CENTER_Y + 85      # larger y = lower on screen = the line coming up next

    FADE = r"{\fad(200,250)}"  # quick fade in/out for the context lines -- this is what gives
                                # the "one by one fading" motion feel the user asked for

    for i, chunk in enumerate(chunks):
        start_ms = int(chunk[0]["start"] * 1000)
        end_ms = int(chunk[-1]["end"] * 1000)

        # CENTER: current chunk, karaoke-highlighted, full opacity
        subs.events.append(pysubs2.SSAEvent(
            start=start_ms, end=end_ms, style="Center", text=make_karaoke_text(chunk)
        ))

        # ABOVE: previous chunk, dimmed, static preview during this window
        if i > 0:
            prev_text = plain_text(chunks[i - 1])
            ev = pysubs2.SSAEvent(
                start=start_ms, end=end_ms, style="Above",
                text=rf"{FADE}{{\alpha&H70&\pos(960,{ABOVE_Y})}}{prev_text}",
            )
            subs.events.append(ev)

        # BELOW: next chunk, dimmed, static preview during this window
        if i < len(chunks) - 1:
            next_text = plain_text(chunks[i + 1])
            ev = pysubs2.SSAEvent(
                start=start_ms, end=end_ms, style="Below",
                text=rf"{FADE}{{\alpha&H70&\pos(960,{BELOW_Y})}}{next_text}",
            )
            subs.events.append(ev)

    os.makedirs("captions", exist_ok=True)
    out_path = f"captions/{slug}.ass"
    subs.save(out_path)
    print(f"✅ Captions saved: {out_path} ({len(words)} words, {len(chunks)} lines)")

if __name__ == "__main__":
    main(sys.argv[1])
