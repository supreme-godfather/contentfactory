"""Content packs: complete, versioned format definitions.

A pack is the answer to "what kind of reel is this?". It carries everything
the pipeline needs to make one: the writer directives, the visual anchor that
keeps every scene in the same world, the hook grammar, the beat map, the
dialogue style, the caption treatment, the title formula and the hashtag pool.

Two packs ship as series-ready formats:

- ``cinematic_boss_series`` — a recurring, original cinematic character in a
  multi-part story. The character is locked with a reference sheet that becomes
  the first frame of every generated shot, so the same character appears in
  every scene. The format follows the viral "cinematic boss story" wave
  (dramatic entrances, absurd escalation, a hard cliffhanger) without copying
  any existing creator's character.
- ``desi_boss_monologue`` — the script-only version. One narrator, contrast
  humour, exaggerated money comparisons, no character image needed. It runs on
  the current engine as-is and costs the least per part.

The six legacy niche presets are kept as packs so nothing that already works
changes behaviour.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Pack:
    pack_id: str
    label: str
    summary: str
    writer_directives: str = ""
    visual_anchor: str = ""
    character_prompt: str = ""
    needs_character_image: bool = False
    hook_style: str = ""
    hook_types: tuple[str, ...] = ()
    beats: tuple[str, ...] = ()
    dialogue_style: str = ""
    line_budget: str = ""
    caption_style: str = ""
    title_formula: str = ""
    hashtag_pool: tuple[str, ...] = ()
    voice_direction: str = ""
    series_ready: bool = False
    default_parts: int = 1
    legacy_niche: str = ""


LEGACY_VISUAL_ANCHOR = (
    "Consistent original Hindi dark-cinematic faceless Reel style: charcoal "
    "black, muted gold and deep red palette; dramatic side lighting; wolf, "
    "marble, newspaper, clock, crowd and archival-symbol motifs; bold high-"
    "contrast typography; original artwork only, no real person's likeness, "
    "no copied creator or stock-video look."
)

_LEGACY_DIRECTIVES = (
    "Write in simple, conversational Hindi that a 25-year-old understands in one "
    "sitting. Use a concrete example, not a lecture. Keep sentences short."
)

PACKS: dict[str, Pack] = {
    # ── Legacy niches, kept so existing behaviour is unchanged ─────────────
    "hindi_facts": Pack(
        pack_id="hindi_facts",
        label="Hindi educational facts explainer",
        summary="Facts-first explainer style",
        writer_directives=_LEGACY_DIRECTIVES,
        visual_anchor=LEGACY_VISUAL_ANCHOR,
        hook_style="A surprising fact the viewer did not know",
        hook_types=("stat", "myth", "question"),
        beats=("hook", "problem", "disruption", "how_it_works", "truth_payoff", "discussion"),
        line_budget="130-150 words, ~60 seconds",
        title_formula="Plain factual title, no hype words",
        hashtag_pool=("#HindiFacts", "#Facts", "#Education", "#Hindi", "#Learn"),
        legacy_niche="hindi_facts",
    ),
    "hindi_science": Pack(
        pack_id="hindi_science",
        label="Hindi science explainer",
        summary="Science-first explainer style",
        writer_directives=_LEGACY_DIRECTIVES + " Explain one science idea clearly.",
        visual_anchor=LEGACY_VISUAL_ANCHOR,
        hook_style="A mind-bending science fact",
        hook_types=("stat", "question", "paradox"),
        beats=("hook", "problem", "disruption", "how_it_works", "truth_payoff", "discussion"),
        line_budget="130-150 words, ~60 seconds",
        title_formula="Question or single fact as the title",
        hashtag_pool=("#Science", "#Hindi", "#Facts", "#Education"),
        legacy_niche="hindi_science",
    ),
    "hindi_history": Pack(
        pack_id="hindi_history",
        label="Hindi history & untold stories explainer",
        summary="Untold-history storytelling style",
        writer_directives=_LEGACY_DIRECTIVES + " Tell it as an untold story with people and stakes.",
        visual_anchor=LEGACY_VISUAL_ANCHOR,
        hook_style="An untold piece of history that changes the story",
        hook_types=("paradox", "question", "visual_reveal"),
        beats=("hook", "problem", "disruption", "how_it_works", "truth_payoff", "discussion"),
        line_budget="140-160 words, ~60 seconds",
        title_formula="A date or place plus a mystery",
        hashtag_pool=("#History", "#Hindi", "#Facts", "#Stories"),
        legacy_niche="hindi_history",
    ),
    "hindi_money": Pack(
        pack_id="hindi_money",
        label="Hindi money & business explainer",
        summary="Money and business explainer style",
        writer_directives=_LEGACY_DIRECTIVES + " Use rupee amounts a viewer can picture.",
        visual_anchor=LEGACY_VISUAL_ANCHOR,
        hook_style="A money comparison that stings",
        hook_types=("stat", "question"),
        beats=("hook", "problem", "disruption", "how_it_works", "truth_payoff", "discussion"),
        line_budget="130-150 words, ~60 seconds",
        title_formula="Amount plus consequence",
        hashtag_pool=("#Money", "#Business", "#Hindi", "#Finance"),
        legacy_niche="hindi_money",
    ),
    "hindi_motivation": Pack(
        pack_id="hindi_motivation",
        label="Hindi motivation & life lessons",
        summary="Discipline and mindset lessons",
        writer_directives=_LEGACY_DIRECTIVES + " Be firm, not preachy.",
        visual_anchor=LEGACY_VISUAL_ANCHOR,
        hook_style="A blunt line about what is actually holding the viewer back",
        hook_types=("question", "stat"),
        beats=("hook", "problem", "disruption", "how_it_works", "truth_payoff", "discussion"),
        line_budget="130-150 words, ~60 seconds",
        title_formula="Short imperative statement",
        hashtag_pool=("#Motivation", "#Discipline", "#Hindi", "#Mindset"),
        legacy_niche="hindi_motivation",
    ),
    "hindi_dark_psychology": Pack(
        pack_id="hindi_dark_psychology",
        label="Hindi dark psychology, mindset & history",
        summary="Dark psychology and mindset style",
        writer_directives=_LEGACY_DIRECTIVES + " Keep a calm, cinematic, slightly dark tone.",
        visual_anchor=LEGACY_VISUAL_ANCHOR,
        hook_style="A dark, specific observation about people",
        hook_types=("paradox", "question", "visual_reveal"),
        beats=("hook", "problem", "disruption", "how_it_works", "truth_payoff", "discussion"),
        line_budget="140-160 words, ~60 seconds",
        title_formula="A dark truth with no question mark",
        hashtag_pool=("#Psychology", "#Mindset", "#Hindi", "#Dark"),
        legacy_niche="hindi_dark_psychology",
    ),
    # ── Series formats ─────────────────────────────────────────────────────
    "cinematic_boss_series": Pack(
        pack_id="cinematic_boss_series",
        label="Cinematic Boss Series (original character)",
        summary=(
            "A recurring original cinematic character in a multi-part story: dramatic "
            "entrance, rising stakes, a hard cliffhanger every part, the payoff at the end."
        ),
        writer_directives=(
            "Write a cinematic Hindi story scene for a recurring character: a calm, "
            "unshakable boss who answers with one short, heavy line.\n"
            "Rules: every spoken line is under nine words; lines are dramatic but never "
            "threatening or abusive; the money or power is always absurdly large and "
            "comic, never harmful; the scene ends on a hard cliffhanger that makes the "
            "viewer need the next part; never mention that this is AI or a template; "
            "never use a real person's name or likeness."
        ),
        visual_anchor=(
            "Cinematic 9:16 film look: deep emerald and charcoal palette with warm gold "
            "highlights, volumetric haze, golden-hour rim light, glossy marble and glass "
            "surfaces, slow confident camera push-ins, shallow depth of field, no text "
            "in the artwork, original character only."
        ),
        character_prompt=(
            "A photorealistic character reference sheet of an original anthropomorphic "
            "black panther businessman with a human-like body, sleek black fur, "
            "expressive amber eyes and a calm, faintly amused expression. He wears a deep "
            "emerald velvet three-piece suit, white shirt, gold watch and a heavy gold "
            "chain. Two rows on a plain light-grey studio background: three "
            "head-and-shoulders portraits (front, three-quarter, right profile) on top, "
            "four full-body views (front, three-quarter, right profile, back) below. The "
            "character must look identical in all seven views. Realistic fur, natural "
            "proportions, even lighting, sharp detail. No text, labels or watermarks."
        ),
        needs_character_image=True,
        hook_style="A one-line entrance or challenge that a viewer answers in their head",
        hook_types=("challenge", "authority", "escalation"),
        beats=("entrance", "challenge", "escalation", "reversal", "cliffhanger"),
        dialogue_style="Short, heavy, confident Hindi lines with comic exaggeration.",
        line_budget="70-95 words, 25-35 seconds",
        caption_style="One short punchy word-phrase at a time, big Devanagari.",
        title_formula="A dramatic statement, no question mark, no clickbait.",
        hashtag_pool=("#SheruStyle", "#CinematicReels", "#HindiReels", "#AIArt", "#Reels"),
        voice_direction="Deep, calm, unhurried male Hindi voice with quiet confidence.",
        series_ready=True,
        default_parts=6,
    ),
    "desi_boss_monologue": Pack(
        pack_id="desi_boss_monologue",
        label="Desi Boss Monologue",
        summary=(
            "Script-only boss monologue: ultra-rich outside, simple heart inside. "
            "Contrast humour, exaggerated money lines, no character image needed."
        ),
        writer_directives=(
            "Write a single-voice Hindi monologue for a confident, street-smart boss who "
            "is rich on the outside and simple at heart. The joke is always a contrast: "
            "a 40-crore car and a 10-rupee cutting chai, a private jet and a village "
            "promise, a whole palace and one old habit. Use one exaggerated money line "
            "that rhymes or plays with number words. Keep every line under ten words. "
            "End on a line that flips the meaning. Never mention AI, never use a real "
            "person's name, never be cruel to anyone."
        ),
        visual_anchor=(
            "Cinematic 9:16 mood board: rain-slick street at night, warm chai-stall "
            "lights, deep shadows with amber highlights, luxury reflections, slow "
            "parallax, no text in the artwork, original visuals only."
        ),
        character_prompt="",
        needs_character_image=False,
        hook_style="A blunt contrast line that lands in the first three seconds",
        hook_types=("contrast", "authority", "escalation"),
        beats=("hook", "contrast", "escalation", "twist", "payoff"),
        dialogue_style="Punchy street Hindi with rhythm and rhyme.",
        line_budget="60-85 words, 22-30 seconds",
        caption_style="One bold Devanagari phrase at a time.",
        title_formula="The contrast itself as the title",
        hashtag_pool=("#DesiBoss", "#HindiReels", "#Attitude", "#Reels", "#Facts"),
        voice_direction="Street-smart Hindi male voice, confident and warm.",
        series_ready=True,
        default_parts=6,
    ),
}

SERIES_PACKS: tuple[str, ...] = tuple(
    pack_id for pack_id, pack in PACKS.items() if pack.series_ready
)

DEFAULT_PACK_ID = "hindi_dark_psychology"


def get_pack(pack_id: str | None) -> Pack:
    """Return a pack, falling back to the default when the id is unknown."""
    if pack_id and pack_id in PACKS:
        return PACKS[pack_id]
    return PACKS[DEFAULT_PACK_ID]


def pack_directives(pack: Pack) -> str:
    """Writer instructions for a pack, ready to be injected into the prompt."""
    parts = [pack.writer_directives]
    if pack.hook_style:
        parts.append(f"Opening style: {pack.hook_style}.")
    if pack.beats:
        parts.append("Story beats in order: " + " then ".join(pack.beats) + ".")
    if pack.line_budget:
        parts.append(f"Length: {pack.line_budget}.")
    if pack.dialogue_style:
        parts.append(f"Dialogue style: {pack.dialogue_style}")
    if pack.voice_direction:
        parts.append(f"Narration voice: {pack.voice_direction}")
    return "\n".join(part for part in parts if part)


def pack_visual_prompt(pack: Pack) -> str:
    """The visual anchor appended to every scene prompt."""
    if pack.visual_anchor:
        return pack.visual_anchor
    return LEGACY_VISUAL_ANCHOR
