"""Genre blueprints for the content engine.

Each genre is a data package: a visual engine, a hook style, a retention
shape, and the asset mode the renderer understands. Genres are selected by the
owner, shuffled inside a category, drawn at random from everything, or matched
automatically to a topic.

Nothing here executes code or calls a provider. It only shapes the script.
"""
import hashlib
from random import SystemRandom

# (id, label, category, visual engine, hook style, asset mode, sfx flavour)
GENRES: tuple[dict[str, str], ...] = (
    # ── Stickman & whiteboard ────────────────────────────────────────────
    {"id": "stickman_psych", "label": "Stickman Psychology", "category": "stickman",
     "engine": "Layered stickman figures on a clean background, expressive poses",
     "hook": "A relatable modern frustration, then the character's exaggerated reaction",
     "asset": "stickman_code", "sfx": "pop"},
    {"id": "stickman_caveman", "label": "Caveman Parables", "category": "stickman",
     "engine": "Painted cave-wall scenes with two stick figures",
     "hook": "Drop a modern money or tech problem into a prehistoric setting",
     "asset": "stickman_code", "sfx": "impact"},
    {"id": "stickman_finance_neon", "label": "Neon Finance Stickman", "category": "stickman",
     "engine": "Black canvas, glowing chalk stick figures, neon chart shapes",
     "hook": "A rupee comparison that stings, for example what 1 lakh buys in 2026 versus 2015",
     "asset": "stickman_code", "sfx": "riser"},
    {"id": "stickman_whatif", "label": "What If Stickman", "category": "stickman",
     "engine": "Mood-lit hooded silhouette against a smoky backdrop",
     "hook": "A high-stakes what-if question in the first two seconds",
     "asset": "stickman_code", "sfx": "riser"},
    {"id": "whiteboard_rapid", "label": "Whiteboard Rapid Draw", "category": "stickman",
     "engine": "Hand-drawn stroke reveals that simplify one idea into three boxes",
     "hook": "A complex idea drawn in three boxes within forty-five seconds",
     "asset": "manim_chart", "sfx": "tick"},
    {"id": "stickman_office_satire", "label": "Office Satire Stickman", "category": "stickman",
     "engine": "Multiple stick characters in a cubicle scene",
     "hook": "An unspoken truth about interviews, appraisals or startup life",
     "asset": "stickman_code", "sfx": "pop"},
    # ── Motion graphics & data ───────────────────────────────────────────
    {"id": "hq_manim_explainer", "label": "3Blue1Brown Maths", "category": "motion",
     "engine": "Vector maths animation, equations transforming on screen",
     "hook": "A visual transformation of one equation or one network",
     "asset": "manim_chart", "sfx": "whoosh"},
    {"id": "hq_vox_motion_doc", "label": "Vox Style Documentary", "category": "motion",
     "engine": "Layered paper cutouts, highlight bars, zoom into one document",
     "hook": "Zoom into a highlighted headline or a declassified file",
     "asset": "still_kenburns", "sfx": "riser"},
    {"id": "hq_kinetic_type", "label": "Kinetic Typography", "category": "motion",
     "engine": "Beat-synced bold type filling the frame",
     "hook": "One word slamming in on the first beat",
     "asset": "still_kenburns", "sfx": "impact"},
    {"id": "hq_data_race", "label": "Data Race", "category": "motion",
     "engine": "Animated bar chart race with year counters",
     "hook": "One country's payment network overtaking every rival",
     "asset": "manim_chart", "sfx": "whoosh"},
    {"id": "hq_map_geopolitics", "label": "Geopolitics Map", "category": "motion",
     "engine": "Animated map pans with border and trade-route arrows",
     "hook": "A red arrow zooming into one strategic chokepoint",
     "asset": "still_kenburns", "sfx": "riser"},
    {"id": "hq_isometric_tech", "label": "Isometric Tech", "category": "motion",
     "engine": "Consistent isometric plates with flowing data pulses",
     "hook": "How one payment scan actually clears in a second",
     "asset": "still_kenburns", "sfx": "tick"},
    {"id": "hq_ui_teardown", "label": "App UI Teardown", "category": "motion",
     "engine": "Clean screen capture with tilt, spotlight and callouts",
     "hook": "A hidden dark pattern inside a popular Indian app",
     "asset": "still_kenburns", "sfx": "pop"},
    {"id": "hq_timeline_evolution", "label": "Timeline Evolution", "category": "motion",
     "engine": "Horizontal scrolling timeline with archival plates and a year counter",
     "hook": "Twenty-nine years of game physics in fifty seconds",
     "asset": "still_kenburns", "sfx": "whoosh"},
    # ── Character storytelling ────────────────────────────────────────────
    {"id": "char_short_drama", "label": "Character Short Drama", "category": "story",
     "engine": "One recurring character kept identical across every scene",
     "hook": "Start mid-conflict, in media res",
     "asset": "ai_video", "sfx": "riser"},
    {"id": "char_motion_comic", "label": "Motion Comic", "category": "story",
     "engine": "Sequential comic panels, speech bubbles, camera shake",
     "hook": "A dramatic panel reveal with a tense Hindi narrator",
     "asset": "ai_video", "sfx": "impact"},
    {"id": "char_mythology_cinematic", "label": "Indian Mythology", "category": "story",
     "engine": "Stylised epic artwork with 2.5D parallax and orchestral score",
     "hook": "A lesser-known tactical story from the Mahabharata or Puranas",
     "asset": "ai_video", "sfx": "riser"},
    {"id": "char_mascot_host", "label": "Mascot Host", "category": "story",
     "engine": "A friendly animated mascot over motion visuals",
     "hook": "The mascot addresses the viewer directly in the first second",
     "asset": "still_kenburns", "sfx": "pop"},
    {"id": "char_history_pov", "label": "History First Person", "category": "story",
     "engine": "First-person historical scene with archival still plates",
     "hook": "You wake as a merchant in 1600s Surat, here is how you survive",
     "asset": "ai_video", "sfx": "impact"},
    {"id": "char_crime_scam_doc", "label": "Scam Investigation", "category": "story",
     "engine": "Noir silhouettes, red highlights, evidence-board graphics",
     "hook": "One fake invoice that fooled several banks",
     "asset": "still_kenburns", "sfx": "riser"},
    {"id": "char_shadow_horror", "label": "Shadow Horror", "category": "story",
     "engine": "High-contrast silhouettes, fog, spatial sound",
     "hook": "A whispered opening line, then sudden silence",
     "asset": "ai_video", "sfx": "riser"},
    {"id": "char_modern_fable", "label": "Modern Fable", "category": "story",
     "engine": "Warm storybook watercolour characters, gentle camera drift",
     "hook": "A short relatable dilemma with an unexpected ending",
     "asset": "still_kenburns", "sfx": "pop"},
    # ── Gaming & transformative ──────────────────────────────────────────
    {"id": "gta6_hidden_details", "label": "GTA Hidden Details", "category": "gaming",
     "engine": "Owner-recorded gameplay with zoom callouts on one detail",
     "hook": "A physics detail hidden in the game world, watch closely",
     "asset": "gameplay_zoom", "sfx": "whoosh"},
    {"id": "gta6_vs_gtav", "label": "GTA V vs VI", "category": "gaming",
     "engine": "Vertical split-screen with a running comparison score",
     "hook": "An instant physics or police AI comparison at 0.0 seconds",
     "asset": "gameplay_zoom", "sfx": "impact"},
    {"id": "gta6_mythbusters", "label": "GTA Myth Lab", "category": "gaming",
     "engine": "Gameplay tests with CONFIRMED or BUSTED animated stamps",
     "hook": "Can that actually be done? We test three ways",
     "asset": "gameplay_zoom", "sfx": "impact"},
    {"id": "gta6_pro_guides", "label": "GTA Survival Guide", "category": "gaming",
     "engine": "Gameplay with map markers and numbered step badges",
     "hook": "Stop wasting cash on day one, buy this first",
     "asset": "gameplay_zoom", "sfx": "tick"},
    {"id": "gaming_news_60s", "label": "Gaming News Brief", "category": "gaming",
     "engine": "Official press stills with kinetic headline cards",
     "hook": "One patch changed this mechanic overnight, here is what broke",
     "asset": "still_kenburns", "sfx": "whoosh"},
    {"id": "gaming_tactical_clip", "label": "Tactical Breakdown", "category": "gaming",
     "engine": "Freeze-frame telestrator arrows over permitted footage",
     "hook": "Freeze frame right before the clutch moment",
     "asset": "gameplay_zoom", "sfx": "impact"},
    {"id": "gaming_hw_bench", "label": "Hardware Bench", "category": "gaming",
     "engine": "Benchmark capture with a live FPS overlay",
     "hook": "Can a budget PC hold sixty frames? Here is the answer",
     "asset": "gameplay_zoom", "sfx": "tick"},
    # ── Business, finance, tech ──────────────────────────────────────────
    {"id": "biz_case_study", "label": "Business Case Study", "category": "business",
     "engine": "Brand visuals with clean KPI overlays and fast cuts",
     "hook": "How one small brand beat a giant using a single trick",
     "asset": "still_kenburns", "sfx": "whoosh"},
    {"id": "fin_visual_math", "label": "Finance Visual Math", "category": "business",
     "engine": "Growing curves and clean Hindi narration over them",
     "hook": "Five thousand a month at twenty-two versus thirty",
     "asset": "manim_chart", "sfx": "tick"},
    {"id": "tech_ai_tutorials", "label": "AI Tool Tutorials", "category": "business",
     "engine": "Zoomed screen recording with a cursor spotlight",
     "hook": "Stop paying for this, this free tool does it better",
     "asset": "still_kenburns", "sfx": "pop"},
    {"id": "tech_buyer_guide", "label": "Don't Buy Before Watching", "category": "business",
     "engine": "Spec comparison cards with verdict pills",
     "hook": "Three flaws in a viral phone nobody mentions",
     "asset": "still_kenburns", "sfx": "pop"},
    {"id": "fin_crash_autopsy", "label": "Crash Autopsy", "category": "business",
     "engine": "Red candlestick drops, timeline markers, news cutouts",
     "hook": "In forty-eight hours retail lost crore, here is the trap",
     "asset": "manim_chart", "sfx": "impact"},
    {"id": "eng_megaproject", "label": "Megaproject Breakdown", "category": "business",
     "engine": "Schematic pans with blueprint overlays and scale comparisons",
     "hook": "Why this bridge can survive an earthquake and a blast",
     "asset": "still_kenburns", "sfx": "riser"},
    {"id": "tech_scam_alert", "label": "Scam Alert", "category": "business",
     "engine": "Phone UI mockups with red warning banners",
     "hook": "If you get this collect request, never tap accept",
     "asset": "still_kenburns", "sfx": "impact"},
    # ── Science, facts, news ─────────────────────────────────────────────
    {"id": "sci_rapid_doc", "label": "Rapid Science Facts", "category": "facts",
     "engine": "Public-domain space footage with concept plates",
     "hook": "A telescope just spotted something impossible",
     "asset": "still_kenburns", "sfx": "riser"},
    {"id": "fact_myth_buster", "label": "Myth Buster", "category": "facts",
     "engine": "Bold claim card, shatter effect, evidence visual",
     "hook": "Everything you learned about this is wrong",
     "asset": "still_kenburns", "sfx": "impact"},
    {"id": "sci_body_psych", "label": "Body & Mind Visualiser", "category": "facts",
     "engine": "Stylised anatomy and brain diagrams with pulsing arrows",
     "hook": "What happens inside your brain after three days without sleep",
     "asset": "manim_chart", "sfx": "whoosh"},
    {"id": "fact_how_made", "label": "How Things Are Made", "category": "facts",
     "engine": "Stylised cutaway steps and satisfying process shots",
     "hook": "The surprising material behind an ordinary object",
     "asset": "still_kenburns", "sfx": "tick"},
    {"id": "sci_survival", "label": "Survival Science", "category": "facts",
     "engine": "Dynamic diagram overlays and a countdown risk meter",
     "hook": "If the lift cable snaps, jumping will not save you",
     "asset": "manim_chart", "sfx": "riser"},
    {"id": "news_explainer_60s", "label": "News In 60 Seconds", "category": "facts",
     "engine": "Source badge overlay, map and stat cards, fast breakdown",
     "hook": "A new rule starts next Monday, here is what changes",
     "asset": "still_kenburns", "sfx": "whoosh"},
    # ── Interactive, listicle, repurposed ────────────────────────────────
    {"id": "interactive_quiz", "label": "5 Second Quiz", "category": "interactive",
     "engine": "Clean quiz template with a ticking clock and reveal chime",
     "hook": "Most people fail question three, comment your score",
     "asset": "still_kenburns", "sfx": "tick"},
    {"id": "listicle_countdown", "label": "Top 5 Countdown", "category": "interactive",
     "engine": "Progress bar with rank badges from five down to one",
     "hook": "Number one is blurred until the final seconds",
     "asset": "still_kenburns", "sfx": "riser"},
    {"id": "comparison_vs_battle", "label": "A vs B Battle", "category": "interactive",
     "engine": "Split-screen cards with a running point tally",
     "hook": "The real five-year cost of one versus the other",
     "asset": "still_kenburns", "sfx": "impact"},
    {"id": "podcast_audiogram", "label": "Podcast Clip", "category": "interactive",
     "engine": "Active speaker crop, waveform, burned captions",
     "hook": "Cut straight into the sharpest two-second line",
     "asset": "still_kenburns", "sfx": "pop"},
    {"id": "book_visual_notes", "label": "Book In 60 Seconds", "category": "interactive",
     "engine": "Sketch-style diagrams, takeaway cards, chapter markers",
     "hook": "I read a long book so you can use three rules today",
     "asset": "manim_chart", "sfx": "tick"},
    {"id": "longform_distiller", "label": "Long To Short Distiller", "category": "interactive",
     "engine": "A re-recorded vertical hook bridging to the parent video",
     "hook": "The one scene from the long video worth watching",
     "asset": "still_kenburns", "sfx": "whoosh"},
)

GENRE_BY_ID = {genre["id"]: genre for genre in GENRES}
GENRE_CATEGORIES = tuple(dict.fromkeys(genre["category"] for genre in GENRES))
CATEGORY_LABELS = {
    "stickman": "Stickman & Whiteboard",
    "motion": "Motion Graphics & Data",
    "story": "Character Storytelling",
    "gaming": "Gaming & Transformative",
    "business": "Business, Finance & Tech",
    "facts": "Science, Facts & News",
    "interactive": "Interactive & Repurposed",
}

GENRE_MODES = ("specific", "shuffle", "random", "auto")


def get_genre(genre_id: str) -> dict[str, str] | None:
    return GENRE_BY_ID.get(genre_id)


def genres_in_category(category: str) -> list[dict[str, str]]:
    return [genre for genre in GENRES if genre["category"] == category]


def random_genre(category: str | None = None) -> dict[str, str]:
    pool = genres_in_category(category) if category else list(GENRES)
    if not pool:
        raise ValueError("No genre is available for that category")
    return SystemRandom().choice(pool)


def match_genre_for_topic(topic: str, category: str | None = None) -> dict[str, str]:
    """Pick the genre whose words best match a topic, deterministically."""
    pool = genres_in_category(category) if category else list(GENRES)
    if not pool:
        raise ValueError("No genre is available for that category")
    haystack = topic.casefold()
    best = None
    best_score = -1
    for genre in pool:
        keywords = {
            "gaming": ("gta", "game", "gameplay", "play", "gaming"),
            "business": ("startup", "business", "brand", "money", "invest", "company", "product", "app", "tool", "laptop", "phone", "upi", "salary", "crypto", "stock", "bank"),
            "facts": ("science", "space", "isro", "nasa", "brain", "body", "myth", "fact", "history", "made", "survive", "news", "rule"),
            "story": ("story", "mythology", "drama", "comic", "history", "fable", "crime", "horror", "character"),
            "motion": ("math", "equation", "chart", "data", "map", "isometric", "app", "ui", "timeline", "motion"),
            "stickman": ("psychology", "mindset", "habit", "caveman", "neon", "what if", "whiteboard", "office", "satire"),
        }.get(genre["category"], ())
        score = sum(1 for word in keywords if word in haystack)
        if score > best_score:
            best = genre
            best_score = score
    if best is None or best_score <= 0:
        digest = int(hashlib.sha256(topic.encode("utf-8")).hexdigest()[:8], 16)
        return pool[digest % len(pool)]
    return best


def genre_directives(genre: dict[str, str] | None) -> str:
    """A prompt block that pushes the writer and the visuals toward the genre."""
    if not genre:
        return ""
    return (
        "GENRE DIRECTIVE (follow it closely)\n"
        f"Genre: {genre['label']}.\n"
        f"Visual engine: {genre['engine']}.\n"
        f"Hook style: {genre['hook']}.\n"
        f"Visual asset mode: {genre['asset']}.\n"
        f"Sound cue flavour: {genre['sfx']}.\n"
        "Write for this visual language and hook; the requester will build the visuals to match.\n"
    )
