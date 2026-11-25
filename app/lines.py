"""The signature line library.

The Sheru-format reels live on one-liners: a swagger sentence, an absurd
number, a dismissive reply. This module holds that register in one place so
the writer can aim at it instead of inventing generic banter, and so the
quality gate can check a reel against it.

The lines are written for this engine: they are the *register* to imitate, not
a script to copy.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class LineCategory:
    key: str
    label: str
    intent: str
    lines: tuple[str, ...]


CATEGORIES: tuple[LineCategory, ...] = (
    LineCategory(
        key="boss_entry",
        label="The boss arrives",
        intent="announce presence, dismiss the room",
        lines=(
            "कुर्सी ख़ाली करिए, असली बॉस आ गया।",
            "रास्ता छोड़ो… शेरू भाई की एंट्री है।",
            "शोर क्यों बंद हो गया? बस मैं आया हूँ।",
            "जंगल हो या शहर… राजा एक ही होता है।",
            "पहले गाड़ी देखो, फिर बात करो।",
            "गेट खोलो। शेरू भाई घर आए हैं।",
        ),
    ),
    LineCategory(
        key="attitude",
        label="Billionaire swagger",
        intent="mock the other side with comic arrogance",
        lines=(
            "एक अरब? ठीक है। दो अरब।",
            "दस हज़ार करोड़? डबल लिखो।",
            "चेक मत माँगो, सीधा बैंक ख़रीद लो।",
            "ऊपर बादल, नीचे दुनिया, हाथ में चाय।",
            "हम बोलते कम हैं, मार्केट ज़्यादा हिलती है।",
            "अपना टाइम आता नहीं, हम ख़ुद ले आते हैं।",
            "मेरी बात सुनोगे, तो सब ठीक हो जाएगा।",
            "अपना टाइम आता नहीं, हम ख़ुद ले आते हैं।",
        ),
    ),
    LineCategory(
        key="street",
        label="Street and desi contrast",
        intent="the humble heart under the luxury",
        lines=(
            "सुपरकार 40 करोड़ की, पर कटिंग चाय 10 रुपये की।",
            "बारिश रुक जाएगी। शेरू भाई नहीं।",
            "तूफ़ान से मत डराओ… हम ख़ुद तूफ़ान हैं।",
            "कितना भी बड़ा हो जाओ… टपरी की चाय नहीं छोड़नी।",
            "शादी किसी की भी हो… एंट्री शेरू भाई की।",
            "चिंटू, कैमरा सीधा पकड़… आज भाई का व्लॉग है।",
            "DJ वाले, बेस डबल कर दे।",
            "ट्रैफ़िक जाम नीचे छोड़ आए।",
        ),
    ),
    LineCategory(
        key="family",
        label="Family and village",
        intent="soft power, the rare warm beat",
        lines=(
            "एडमिशन नहीं, पूरा स्कूल ले लिया।",
            "जहाँ से निकले थे, वहीं लौट आए… गाड़ी बदल गई।",
            "केक बाद में। पहले चाबी पकड़ो।",
            "गाड़ियाँ पाँच हैं… पर शेर एक ही है।",
            "बेटा, प्रिंसिपल का रूम आज से तुम्हारा।",
            "चाचा को बोलो, आज पूरे गाँव की दावत है।",
        ),
    ),
    LineCategory(
        key="rival",
        label="The rival answers",
        intent="the shocked, competitive reply",
        lines=(
            "पचास करोड़?! सौ करोड़, यह मेरी चुनौती है।",
            "सौ करोड़?! पाँच सौ — अब मेरी बारी।",
            "तू पागल है! मैंने तो हज़ार करोड़ लगाए।",
            "हज़ार?! दस हज़ार! अब बोल कितना है।",
            "तुझे नहीं पता मेरा कितना है।",
            "इतना कम? शेरू भाई ने सोचा भी ना।",
        ),
    ),
)

MONEY_GRAMMAR = (
    "Money lines use real Hindi number words and then one impossible unit: "
    "पचास करोड़, सौ करोड़, पाँच सौ करोड़, एक हज़ार करोड़, दस हज़ार करोड़, "
    "दस अरब डॉलर. The jump between two numbers is the joke, so every "
    "escalation must be visibly larger than the one before it."
)

PUNCH_RULES = (
    "DIALOGUE PUNCH RULES\n"
    "1. Make it harsher and funnier: arrogance, dismissal, comic shock.\n"
    "2. Keep the speaker tags and the same number of turns.\n"
    "3. Every line stays under nine words and reads like spoken Hindi.\n"
    "4. The first line must still work as the opening frame in one second.\n"
    "5. The money must escalate every single time.\n"
    "6. End on one quotable line the viewer could screenshot.\n"
    "7. Never threaten, never curse, never use a real person's name.\n"
    "8. No narration, no stage directions, no subscribe line, no viewer question."
)


def register_directives() -> str:
    """The target register, written into the writer prompt."""
    blocks: list[str] = []
    for category in CATEGORIES:
        blocks.append(f"{category.label} — {category.intent}:")
        blocks.extend(f"  - {line}" for line in category.lines[:6])
    return (
        "Write in this register (the flavour to imitate, never copy a line "
        "word for word):\n"
        + "\n".join(blocks)
        + "\n"
        + MONEY_GRAMMAR
    )


def signature_line_for(speaker: str) -> str | None:
    """A one-liner the character could say, used as a style seed."""
    speaker_key = speaker.strip().casefold()
    if speaker_key in {"शेरू भाई", "शेरू", "sheru bhai", "white_lion_boss"}:
        return CATEGORIES[0].lines[0]
    if speaker_key in {"ग्रीम पैंथर", "emerald panther", "emerald_panther"}:
        return CATEGORIES[4].lines[0]
    if speaker_key in {"भालू भाई", "bear_mafia"}:
        return CATEGORIES[4].lines[2]
    return CATEGORIES[2].lines[0]
