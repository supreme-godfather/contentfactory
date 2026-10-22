"""Storyboard, caption and timing planner for generated videos.

Pure and deterministic — no I/O, no providers, never raises on odd input.
The ideas here are standard explainer craft, implemented in our own code:

- Script-first timing: narration pace is ~2.3 words/second (~140 wpm), and
  the audio drives the video length — scenes are never stretched to fit.
- One core idea per scene, following the retention arc
  hook → problem → disruption → how it works → truth/payoff → discussion.
- A single frozen STYLE_ANCHOR string is attached to every scene so all
  visuals share one character design, palette and typography (consistency
  is what makes a series feel like one channel, not a stock montage).
- Captions are mandatory (most Shorts play muted): max 2 lines of ~42
  characters, at least 1 second on screen, timed from the same word clock
  as the scenes so captions, voice and visuals cannot drift apart.
"""
import re

WORDS_PER_SECOND = 2.3
TARGET_DURATION_SECONDS = 60
MIN_CUE_SECONDS = 1.0
MAX_CAPTION_LINE_CHARS = 42
MAX_CAPTION_LINES = 2

STYLE_ANCHOR = (
    "Consistent original Hindi explainer style: same clean 2D illustrated "
    "character design, same colour palette and same typography in every "
    "scene; original artwork only, no real person's likeness, no copied "
    "creator or stock-video look."
)

# (stage, share of total duration, visual direction, narration job)
_STAGES: tuple[tuple[str, float, str, str], ...] = (
    (
        "hook",
        0.05,
        "Open on the single most surprising visual for the topic; big readable headline text; no intro logo, no greeting.",
        "Golden hook: a question or paradox in the first 3 seconds.",
    ),
    (
        "problem",
        0.15,
        "Show the everyday problem in one clear scene; keep the frame uncluttered, one idea only.",
        "Name the viewer's problem in their own words.",
    ),
    (
        "disruption",
        0.15,
        "Contrast visual: 'what people think' on one side, 'the real truth' on the other; visual change on the reveal.",
        "Shatter the common assumption in one sentence.",
    ),
    (
        "how_it_works",
        0.40,
        "Step-by-step reveal, one step per visual change every 2-3 seconds; diagrams or objects appear in reading order.",
        "Explain the real mechanism in 1-3 concrete steps, simple Hindi.",
    ),
    (
        "truth_payoff",
        0.15,
        "Calm, confident frame; the one lesson shown as a single memorable visual.",
        "Deliver the ultimate truth / lesson the viewer can repeat.",
    ),
    (
        "discussion_cta",
        0.10,
        "End card: one genuine question in large text, then the channel subscribe/follow closing card.",
        "Ask one discussion question, then the fixed closing line.",
    ),
)


def word_count(text: str | None) -> int:
    if not text:
        return 0
    return len(text.split())


def estimate_duration_seconds(script: str | None) -> float:
    """Narration length at ~2.3 words/second, rounded to 0.1s."""
    return round(word_count(script) / WORDS_PER_SECOND, 1)


def _sentences(script: str) -> list[str]:
    parts = re.split(r"(?<=[।?!.\n])\s+", script.strip())
    return [part.strip() for part in parts if part and part.strip()]


def caption_cues(script: str | None) -> list[dict[str, object]]:
    """Caption cues timed from the narration word clock.

    Each cue is at most 2 lines x 42 characters and stays on screen at
    least MIN_CUE_SECONDS; cue boundaries follow sentence boundaries
    where possible.
    """
    if not script or not script.strip():
        return []
    max_chars = MAX_CAPTION_LINE_CHARS * MAX_CAPTION_LINES
    chunks: list[str] = []
    for sentence in _sentences(script):
        words = sentence.split()
        current: list[str] = []
        for word in words:
            candidate = " ".join(current + [word])
            if current and len(candidate) > max_chars:
                chunks.append(" ".join(current))
                current = [word]
            else:
                current.append(word)
        if current:
            chunks.append(" ".join(current))

    cues: list[dict[str, object]] = []
    cursor = 0.0
    for index, text in enumerate(chunks, start=1):
        duration = max(MIN_CUE_SECONDS, len(text.split()) / WORDS_PER_SECOND)
        cues.append(
            {
                "index": index,
                "start_seconds": round(cursor, 1),
                "end_seconds": round(cursor + duration, 1),
                "text": text,
            }
        )
        cursor += duration
    return cues


def build_storyboard(topic: str, script: str | None) -> list[dict[str, object]]:
    """Six-scene storyboard whose lengths come from the script's own
    estimated narration duration (never below a watchable minimum)."""
    total = max(estimate_duration_seconds(script), 30.0)
    scenes: list[dict[str, object]] = []
    cursor = 0.0
    for index, (stage, share, visual, job) in enumerate(_STAGES, start=1):
        duration = round(total * share, 1)
        scenes.append(
            {
                "scene": index,
                "stage": stage,
                "start_seconds": round(cursor, 1),
                "duration_seconds": duration,
                "narration_job": job,
                "visual_direction": visual,
                "style_anchor": STYLE_ANCHOR,
                "on_screen_text": topic if stage == "hook" else "",
            }
        )
        cursor += duration
    return scenes


def script_structure(script: str | None) -> dict[str, object]:
    """Cheap structural checks the quality gate turns into warnings."""
    text = (script or "").strip()
    first_block = text.split("\n", 1)[0] if text else ""
    lowered = text.lower()
    return {
        "word_count": word_count(text),
        "estimated_duration_seconds": estimate_duration_seconds(text),
        "has_hook": bool(text) and ("?" in first_block or "!" in first_block or "रुकिए" in first_block),
        "has_discussion_cta": ("comment" in lowered) or ("कमेंट" in text) or ("आपकी राय" in text),
        "has_closing_line": "subscribe" in lowered and "follow" in lowered,
    }
