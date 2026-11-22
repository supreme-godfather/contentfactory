"""Professional Hindi/Hinglish script engine with a safe deterministic fallback.

The script is the product. A weak script is the single biggest reason a good
video fails, so the writer here follows a proven retention discipline:

- no greeting, no channel intro: the first sentence is the hook
- one curiosity loop opened early and closed only near the end
- a pattern interrupt every six to eight seconds
- natural urban spoken Hindi, breathable sentences, numbers written as words
- only facts that were supplied, never invented statistics or quotes
- one sharp question that loops back into the opening line

Quality is enforced in two passes when the cheap, high-volume MiniMax route is
selected: a retention draft followed by a strict editor that rewrites only what
fails the checklist. Every provider error degrades to the built-in template, so
the factory never fails because a model was unavailable.
"""
import json
import logging
import re

import httpx

logger = logging.getLogger(__name__)

CLOSING_LINES = (
    "ऐसी और कहानियों के लिए Subscribe करें और Instagram पर Follow करना न भूलें।"
)

MINIMAX_CHAT_URL = "https://api.minimax.io/v1/chat/completions"
MINIMAX_SCRIPT_MODEL = "MiniMax-M3.1-Flash"

WRITER_SYSTEM = (
    "You are an elite Hindi/Hinglish short-form video retention writer. "
    "You write narration only: no headings, no markdown, no stage directions, "
    "no emoji, no channel introduction."
)

RETAIL_RULES = """
NON-NEGOTIABLE RULES
1. Start at 0.0 seconds. Never greet, never introduce the channel, never say
   "in this video" or "aaj hum baat karenge".
2. The very first sentence is the hook: a specific number, a paradox, a hidden
   mistake, or a direct question the viewer must answer. No generic openers.
3. Open exactly one curiosity loop between second 2.5 and second 7.0 and close
   it only in the final 15% of the script.
4. Every 6-8 seconds land a pattern interrupt: a counter-fact, a twist, a new
   angle, or a sharp visual shift.
5. Write natural urban Indian spoken Hindi in Devanagari. Keep natural English
   tech, business and gaming words, but write tricky symbols and numbers as
   words ("पचास लाख रुपये", never "50L").
6. Sentences of 8-14 words maximum. Use "—" for a beat and "..." for a
   trailing thought, so the voice actor can breathe.
7. Use only the facts you were given. Never invent statistics, quotes, dates,
   studies, or names.
8. Land one crisp, slightly polarizing question that grammatically loops back
   into sentence one.
9. End with exactly these closing lines and nothing after them:
""".strip()

CRITIC_SYSTEM = (
    "You are the final retention editor for a professional Hindi channel. "
    "You return only the final narration, never commentary or scores."
)

CRITIC_RULES = """
AUDIT AND FIX, THEN RETURN ONLY THE FINAL NARRATION
- Hook strength in the first two seconds: specific, surprising, zero filler.
- The curiosity loop is opened early and genuinely closed near the end.
- A pattern interrupt lands every 6-8 seconds.
- Natural spoken Hindi/Hinglish: no stiff Sanskritised Hindi, no robotic
  phrasing, no filler such as "तो दोस्तों" or "चलिए जानते हैं".
- Short breathable sentences of 8-14 words.
- Every fact is traceable to the supplied facts. No new numbers, names, quotes,
  or studies.
- One payoff worth the watch, and one polarizing question that loops back to
  the hook.
- The ending contains the exact required closing line.
Preserve the topic, the language and the closing line. Never pad, never
lengthen for the sake of length.
""".strip()

METADATA_SYSTEM = (
    "You write publishing metadata for short-form video. "
    "You return only compact JSON. "
    "Answer immediately with the JSON; keep your thinking short."
)

RESEARCH_SYSTEM = (
    "You research hookable angles for Hindi short-form video. "
    "You return only compact JSON and never invent facts. "
    "Answer immediately with the JSON; keep your thinking short."
)


def _template_script(topic: str) -> str:
    """Shorts template (~60s): the five-stage retention arc in prose."""
    return (
        # 1. Golden hook (first 3 seconds): a question the viewer cannot skip.
        f"रुकिए! क्या आप जानते हैं कि {topic} के बारे में सबसे कम लोग जो बात "
        "जानते हैं, वही सबसे ज़्यादा हैरान करने वाली है?\n\n"
        # 2. Disrupt the assumption.
        f"ज़्यादातर लोग सोचते हैं कि {topic} बस एक साधारण-सा विषय है। "
        "लेकिन असली सच इससे बिल्कुल अलग है।\n\n"
        # 3. Insider facts: the real mechanism, in simple Hindi.
        f"इस वीडियो में हम {topic} के मुख्य तथ्य, उसके पीछे की असली वजह और "
        "उसका असर — तीनों को आसान हिंदी में, बिना घुमाए-फिराए समझेंगे।\n\n"
        # 4. Ultimate truth / lesson.
        "सबसे बड़ी सीख यह है कि "
        f"{topic} को समझना सिर्फ़ जानकारी नहीं है — "
        "यह आपके रोज़ के फ़ैसलों को भी बेहतर बना सकता है।\n\n"
        # 5. Discussion CTA: one genuine question that invites comments.
        f"अब आपकी बारी — {topic} के बारे में आपकी राय क्या है? "
        "Comment में ज़रूर बताइए।\n\n"
        # 6. Fixed closing line (exact).
        f"{CLOSING_LINES}"
    )


def _template_script_long(topic: str) -> str:
    """Long-form template (~4 min): same arc, with real chapters between
    the hook and the payoff — background, three deep facts, a worked
    example, a myth-versus-fact section and a summary."""
    return (
        f"रुकिए! क्या आप जानते हैं कि {topic} के बारे में सबसे कम लोग जो बात "
        "जानते हैं, वही सबसे ज़्यादा हैरान करने वाली है? आज के इस वीडियो में हम "
        "सिर्फ़ ऊपर-ऊपर की जानकारी नहीं देंगे — हम इसकी जड़ तक जाएँगे।\n\n"
        f"ज़्यादातर लोग सोचते हैं कि {topic} बस एक साधारण-सा विषय है, जिसे समझने "
        "के लिए किसी ख़ास मेहनत की ज़रूरत नहीं। लेकिन असली सच इससे बिल्कुल अलग है। "
        "जब आप इसके पीछे की कहानी और वजह समझते हैं, तो यही साधारण विषय अचानक "
        "सबसे दिलचस्प बन जाता है।\n\n"
        f"सबसे पहले थोड़ी पृष्ठभूमि समझिए। {topic} कोई एक दिन में बनी चीज़ नहीं है। "
        "इसके पीछे समय, अनुभव और कई छोटे-छोटे बदलावों का लंबा सफ़र है। अक्सर हम "
        "सिर्फ़ नतीजा देखते हैं, लेकिन असली सीख उसी प्रक्रिया में छिपी होती है जो "
        "नतीजे तक पहुँचाती है। यही वजह है कि जो लोग प्रक्रिया समझते हैं, वे दूसरों "
        "से हमेशा एक कदम आगे रहते हैं।\n\n"
        f"अब आते हैं पहले गहरे तथ्य पर। {topic} का सबसे महत्वपूर्ण पहलू वह है जिसे "
        "अधिकतर लोग नज़रअंदाज़ कर देते हैं — इसका सीधा असर। जब यह विषय सही तरीके "
        "से काम करता है, तो इसका प्रभाव सिर्फ़ एक जगह नहीं, बल्कि कई जगहों पर एक "
        "साथ दिखता है। और जब यह गड़बड़ होता है, तो नतीजे भी उतने ही साफ़ दिखते "
        "हैं। इसीलिए विशेषज्ञ हमेशा कहते हैं कि सतह नहीं, व्यवस्था को समझो।\n\n"
        "दूसरा तथ्य और भी दिलचस्प है। "
        f"{topic} में जो चीज़ बाहर से सबसे सरल दिखती "
        "है, अक्सर वही अंदर से सबसे जटिल होती है। इसमें कई छोटे हिस्से एक साथ काम "
        "करते हैं, और हर हिस्सा दूसरे पर निर्भर होता है। एक हिस्सा बदला, तो पूरा "
        "संतुलन बदल जाता है। यही परस्पर निर्भरता इसे इतना मज़बूत भी बनाती है और "
        "इतना नाज़ुक भी।\n\n"
        "तीसरा तथ्य आपके रोज़ के जीवन से जुड़ा है। "
        f"{topic} का असर सिर्फ़ किताबों "
        "या ख़बरों में नहीं मिलता — यह आपके रोज़ के फ़ैसलों, आपकी आदतों और कभी-कभी "
        "आपके ख़र्च तक में दिखता है। जो लोग यह संबंध जल्दी पहचान लेते हैं, वे "
        "बेहतर सवाल पूछते हैं, और बेहतर सवाल ही बेहतर जवाबों की शुरुआत होते हैं।\n\n"
        f"इसे एक आसान उदाहरण से समझिए। कल्पना कीजिए दो लोग {topic} को देखते हैं — "
        "एक सिर्फ़ नतीजा देखता है, दूसरा वजह भी। पहला हर बार हैरान होता है, दूसरा "
        "पहले से तैयार रहता है। फ़र्क़ ज्ञान का नहीं, नज़रिए का है। और अच्छी बात "
        "यह है कि नज़रिया सीखा जा सकता है — बिल्कुल वैसे, जैसे आप इस वीडियो में "
        "सीख रहे हैं।\n\n"
        "अब एक आम ग़लतफ़हमी दूर कर देते हैं। कई लोग मानते हैं कि "
        f"{topic} में सफलता "
        "या समझ सिर्फ़ किस्मत या प्रतिभा से आती है। यह आधा सच है। असली तस्वीर यह "
        "है कि लगातार ध्यान, सही जानकारी और धैर्य — ये तीनों मिलकर वह नतीजा बनाते "
        "हैं जिसे बाहर से किस्मत कहा जाता है।\n\n"
        "तो सबसे बड़ी सीख क्या है? "
        f"{topic} को समझना सिर्फ़ जानकारी इकट्ठा करना "
        "नहीं है — यह सोचना सीखना है। और जो इंसान सोचना सीख जाता है, उसके लिए हर "
        "नया विषय आसान हो जाता है।\n\n"
        f"अब आपकी बारी — {topic} के बारे में आपकी राय क्या है? Comment में ज़रूर "
        "बताइए, और बताइए कि अगले वीडियो में हम किस विषय की जड़ तक जाएँ।\n\n"
        f"{CLOSING_LINES}"
    )


def _template_script_reel(topic: str) -> str:
    lowered_topic = topic.lower()
    if any(
        marker in lowered_topic
        for marker in ("साइबर", "फिशिंग", "phishing", "cybersecurity", "cyber security", "otp")
    ):
        return (
            "क्या बैंक के नाम से आया हर संदेश सच में बैंक से होता है?\n\n"
            "फिशिंग में ठग नकली लिंक भेजते हैं, ताकि आप खुद पासवर्ड या OTP दर्ज कर दें। "
            "संदेश पर दिखने वाला नाम भरोसे का सबूत नहीं होता।\n\n"
            "लिंक खोलने से पहले भेजने वाले और वेबसाइट का पता ध्यान से जाँचें। "
            "अजीब spelling, जल्दी करने का दबाव या इनाम का लालच खतरे के संकेत हैं। "
            "संदेह हो तो लिंक न खोलें—अपने बैंक की official app खुद खोलें।\n\n"
            "OTP, PIN या password किसी के साथ साझा न करें। असली बैंक कर्मचारी भी "
            "आपसे OTP नहीं माँगता। अगर गलती से जानकारी दे दी है, तो तुरंत बैंक की "
            "official helpline पर संपर्क करें और खाते की गतिविधि जाँचें।\n\n"
            "याद रखें: रुकें, जाँचें, फिर tap करें। आपको किसी संदेश में सबसे पहले "
            "कौन-सा खतरे का संकेत दिखेगा? Comment में बताइए।\n\n"
            f"{CLOSING_LINES}"
        )
    return (
        f"रुकिए—क्या {topic} के पीछे कोई ऐसी बात है जिसे हम अक्सर नज़रअंदाज़ कर देते हैं?\n\n"
        f"कई बार {topic} को लोग सिर्फ़ एक सीधी-सादी बात समझते हैं। "
        "लेकिन असली कहानी अक्सर उस छोटे संकेत में छिपी होती है, जिसे हम बिना सोचे मान लेते हैं।\n\n"
        "मनोविज्ञान में एक अहम बात है: हमारा ध्यान सीमित है। इसलिए बार-बार दिखने वाली "
        "कहानी हमें सच जैसी लग सकती है, भले ही हमने उसे जाँचा न हो। इतिहास में भी "
        "संदेश पहुँचाने का माध्यम बदला—लोगों की सोच पर असर डालने की कोशिश नहीं।\n\n"
        f"{topic} को समझने का बेहतर तरीका है: रुककर स्रोत जाँचें, दूसरी व्याख्या खोजें, "
        "और अपने निर्णय खुद लें। किसी को नियंत्रित करना सीखना नहीं—प्रभाव को पहचानना "
        "और अपनी agency बचाना असली सबक है।\n\n"
        f"आपके हिसाब से {topic} में सबसे अनदेखा संकेत कौन-सा है? अपनी राय बताइए।\n\n"
        f"{CLOSING_LINES}"
    )


def _resolve_format_and_niche(fmt: str | None, niche: str | None) -> tuple[str, str]:
    """Fill in format/niche from the Telegram headquarters settings."""
    try:
        from .hq import get_format_key, get_niche_label

        return (fmt or get_format_key()), (niche or get_niche_label())
    except Exception:
        logger.debug("HQ settings lookup failed; using defaults", exc_info=True)
        return (fmt or "short"), (niche or "Hindi educational facts explainer")


def _writer_budget(is_long: bool, is_reel: bool) -> tuple[str, int]:
    if is_long:
        return ("550-700 words, about 4 minutes of narration, organised in chapters: "
                "background, three deep facts (one chapter each), one worked example, "
                "one myth-versus-fact section, and a summary", 1500)
    if is_reel:
        return ("110-140 words, about 45-60 seconds of narration, written for a "
                "dark cinematic vertical Reel", 80)
    return ("135-155 words, about 60 seconds of narration", 80)


def _strip_reasoning(text: str) -> str:
    """Remove any inline reasoning block a model may have left in its answer."""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return cleaned.strip()


def _answer_from_payload(payload: dict) -> str | None:
    """Read the answer out of a chat response, or None when there is none."""
    choices = payload.get("choices") or []
    if not choices:
        return None
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    cleaned = _strip_reasoning(content)
    return cleaned or None


def _minimax_chat(
    api_key: str,
    system: str,
    user: str,
    *,
    max_tokens: int,
    temperature: float,
    model: str = MINIMAX_SCRIPT_MODEL,
) -> str:
    """Call the MiniMax chat route, rotating keys when one is rejected.

    A rejected key must never stop the engine: the next configured key is tried
    and the working one is remembered for the next job. MiniMax M3.1-Flash is a
    reasoning model, so a small token budget can be spent entirely on thinking;
    when that happens the call is retried with a larger budget.
    """
    candidates = [api_key] + [
        key for key in minimax_text_candidates() if key != api_key
    ]
    last_error: Exception | None = None
    for index, key in enumerate(candidates):
        budget = max(int(max_tokens), 512)
        for attempt in range(2):
            try:
                response = httpx.post(
                    MINIMAX_CHAT_URL,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        "temperature": temperature,
                        "max_tokens": budget,
                    },
                    timeout=180,
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {401, 403}:
                    raise
                last_error = exc
                logger.warning(
                    "MiniMax rejected a configured key (%s); trying the next one",
                    exc.response.status_code,
                )
                break
            answer = _answer_from_payload(response.json())
            if answer:
                if index > 0 or key != api_key:
                    _cache_minimax_key(key)
                return answer
            last_error = RuntimeError("The model returned no answer")
            if attempt == 0 and budget < 8000:
                budget = min(8000, budget * 3)
                logger.info(
                    "MiniMax spent the token budget on reasoning; retrying with %s tokens",
                    budget,
                )
                continue
            break
    raise last_error or RuntimeError("No MiniMax text key is configured")


def _parse_json_object(text: str) -> dict[str, object]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("The model did not return a JSON object")
    payload = json.loads(cleaned[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("The model did not return a JSON object")
    return payload


def minimax_text_candidates() -> list[str]:
    """Every configured MiniMax text key, subscription plan first.

    A plan key can expire while a platform key keeps working, so the caller
    tries each candidate until one answers instead of trusting the first one
    that exists.
    """
    from .config import get_settings
    from .provider_credentials import get_provider_secret

    candidates: list[str] = []
    sources = (
        lambda: get_provider_secret("minimax_mplan"),
        lambda: get_provider_secret("minimax"),
        lambda: get_settings().minimax_api_key,
    )
    for source in sources:
        try:
            key = source()
        except Exception:
            key = None
        if key and str(key) not in candidates:
            candidates.append(str(key))
    return candidates


def _cached_minimax_key() -> str | None:
    from .db import ProviderCredential, SessionLocal
    from .encryption import get_fernet

    with SessionLocal() as session:
        credential = session.get(ProviderCredential, "minimax_text_active")
        if credential is None or not credential.secret_encrypted:
            return None
        try:
            return get_fernet().decrypt(credential.secret_encrypted.encode()).decode()
        except Exception:
            session.delete(credential)
            session.commit()
            return None


def _cache_minimax_key(key: str) -> None:
    from .db import ProviderCredential, SessionLocal
    from .encryption import get_fernet

    try:
        encrypted = get_fernet().encrypt(key.encode()).decode()
    except Exception:
        return
    try:
        with SessionLocal() as session:
            credential = session.get(ProviderCredential, "minimax_text_active")
            if credential is None:
                session.add(
                    ProviderCredential(provider="minimax_text_active", secret_encrypted=encrypted)
                )
            else:
                credential.secret_encrypted = encrypted
            session.commit()
    except Exception:
        logger.warning("Could not remember the working MiniMax key", exc_info=True)


def minimax_text_key() -> str | None:
    """Return a MiniMax text key that is known to answer, if there is one."""
    cached = _cached_minimax_key()
    if cached:
        return cached
    candidates = minimax_text_candidates()
    return candidates[0] if candidates else None


def _parse_json_array(text: str) -> list[str]:
    """Accept a JSON array or newline separated lines and return clean strings."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start != -1 and end > start:
        try:
            payload = json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, list):
            return [str(item).strip() for item in payload if str(item).strip()]
        quoted = re.findall(r'"([^"]{8,220})"', cleaned)
        if quoted:
            return [item.strip() for item in quoted if item.strip()]
    lines = [line.strip(" -*\t") for line in cleaned.splitlines()]
    stripped = []
    for line in lines:
        head, _, tail = line.partition(" ")
        if head.rstrip(".):").isdigit() and tail.strip():
            line = tail.strip()
        if 8 <= len(line) <= 220:
            stripped.append(line)
    return stripped


def _series_prompt_block(series_context: dict | None) -> str:
    """Instructions that keep one part of a series continuous with the rest."""
    if not series_context:
        return ""
    part_index = series_context.get("part_index")
    part_count = series_context.get("part_count")
    title = str(series_context.get("part_title") or "").strip()
    beats = [str(item) for item in (series_context.get("part_beats") or []) if str(item).strip()]
    recap = str(series_context.get("previous_recap") or "").strip()
    cliffhanger = str(series_context.get("cliffhanger") or "").strip()
    character = str(series_context.get("series_character") or "").strip()
    lines: list[str] = []
    if title:
        lines.append(f"Story part title: {title}")
    if part_count:
        lines.append(
            f"This is part {part_index} of {part_count} of one continuous story."
        )
    if character:
        lines.append(f"The recurring character: {character}")
    if recap:
        lines.append(
            "Open with one short line that reminds the viewer what happened before: "
            f"{recap}"
        )
    if beats:
        lines.append("Cover these beats in order: " + "; ".join(beats) + ".")
    if cliffhanger:
        lines.append(
            "End the narration with exactly this cliffhanger line and nothing after it: "
            f"{cliffhanger}"
        )
    if not lines:
        return ""
    return "\nSeries context (follow it exactly):\n" + "\n".join(lines) + "\n"


def _enforce_cliffhanger(script: str, series_context: dict | None) -> str:
    """Make sure a part still ends on the cliffhanger the plan promised."""
    if not series_context:
        return script
    cliffhanger = str(series_context.get("cliffhanger") or "").strip()
    if not cliffhanger:
        return script
    body = script.strip()
    if body.endswith(cliffhanger):
        return body
    return f"{body}\n\n{cliffhanger}"


def _script_prompt(
    topic: str,
    niche_label: str,
    budget: str,
    fmt: str,
    facts: str,
    custom_instruction: str | None,
    genre: dict[str, str] | None = None,
    pack_directive: str = "",
    series_context: dict | None = None,
) -> str:
    from .genres import genre_directives
    from .lessons import lessons_block

    direction = ""
    if fmt == "reel":
        direction = (
            "\nExtra direction: vertical Reel, dark cinematic, punchy short lines, "
            "and a visual beat every 2-4 seconds.\n"
        )
    extra = (
        f"\nOwner direction (respect it, keep it original and factual):\n"
        f"{' '.join(custom_instruction.split())[:1200]}\n"
        if custom_instruction
        else ""
    )
    pack_block = f"\nFormat pack (follow it):\n{pack_directive}\n" if pack_directive else ""
    return (
        f"Topic: {topic}\n"
        f"Channel niche: {niche_label}\n"
        f"Format: {fmt}\n"
        f"Length: {budget}\n"
        f"{facts}"
        f"{pack_block}"
        f"{_series_prompt_block(series_context)}"
        f"{genre_directives(genre)}"
        f"{lessons_block()}"
        f"{RETAIL_RULES}\n"
        f"Required closing line (exact, nothing after it):\n{CLOSING_LINES}\n"
        f"{direction}{extra}"
        f"\nWrite the narration now."
    )


def build_script(
    topic: str,
    brand: str = "learn_with_elite_explainer",
    format: str | None = None,
    niche: str | None = None,
    custom_instruction: str | None = None,
    genre: dict[str, str] | None = None,
    pack_id: str | None = None,
    series_context: dict | None = None,
) -> str:
    """Return a ready-to-record narration, or the safe template on any failure."""
    fmt, niche_label = _resolve_format_and_niche(format, niche)
    is_long = fmt == "long"
    is_reel = fmt == "reel"
    budget, min_chars = _writer_budget(is_long, is_reel)
    fallback = (
        _template_script_long(topic)
        if is_long
        else _template_script_reel(topic)
        if is_reel
        else _template_script(topic)
    )

    try:
        from .provider_credentials import (
            get_provider_for_role,
            get_provider_secret,
            no_paid_providers_enabled,
            provider_cooldown_until,
        )

        provider = get_provider_for_role("script")
        if provider == "template":
            return fallback
        if no_paid_providers_enabled() and provider not in {"gemini"}:
            return fallback
        if provider in {"gemini", "huggingface"} and provider_cooldown_until(provider) is not None:
            return fallback
        if provider not in {"openai", "gemini", "huggingface", "minimax"}:
            return fallback
        api_key = (
            minimax_text_key()
            if provider == "minimax"
            else get_provider_secret(provider)
        )
        if not api_key:
            return fallback
    except Exception:
        logger.debug("Provider lookup failed; using template script", exc_info=True)
        return fallback

    from .packs import get_pack, pack_directives

    pack = get_pack(pack_id)
    prompt = _script_prompt(
        topic,
        niche_label,
        budget,
        fmt,
        "",
        custom_instruction,
        genre,
        pack_directive=pack_directives(pack),
        series_context=series_context,
    )
    try:
        if provider == "gemini":
            response = httpx.post(
                "https://generativelanguage.googleapis.com/v1beta/models/"
                "gemini-3.5-flash-lite:generateContent",
                headers={"x-goog-api-key": api_key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "systemInstruction": {"parts": [{"text": WRITER_SYSTEM}]},
                    "generationConfig": {"temperature": 0.7},
                },
                timeout=60,
            )
            response.raise_for_status()
            content = "\n".join(
                part["text"]
                for part in response.json()
                .get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [])
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        elif provider == "huggingface":
            response = httpx.post(
                "https://router.huggingface.co/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "Qwen/Qwen2.5-7B-Instruct",
                    "messages": [
                        {"role": "system", "content": WRITER_SYSTEM},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.7,
                    "max_tokens": 2200 if is_long else 900,
                },
                timeout=90,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        elif provider == "minimax":
            content = _minimax_chat(
                api_key,
                prompt,
                max_tokens=4800 if is_long else 2600,
                temperature=0.78,
            )
        else:
            response = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": WRITER_SYSTEM},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.75,
                    "max_tokens": 2200 if is_long else 900,
                },
                timeout=60,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        if not isinstance(content, str) or len(content.strip()) < min_chars:
            return fallback
        return content.strip()
    except Exception as exc:
        try:
            from .provider_credentials import is_provider_quota_error, mark_provider_quota_error

            if is_provider_quota_error(exc):
                until = mark_provider_quota_error(provider, exc)
                logger.warning(
                    "%s quota/rate limit reached; cooling down until %s",
                    provider,
                    until.isoformat(),
                )
        except Exception:
            logger.debug("Could not record provider quota cooldown", exc_info=True)
        logger.debug("%s script generation failed; using template", provider, exc_info=True)
        return fallback


def build_script_package(
    topic: str,
    brand: str = "learn_with_elite_explainer",
    format: str | None = None,
    niche: str | None = None,
    custom_instruction: str | None = None,
    facts: str = "",
    genre: dict[str, str] | None = None,
    pack_id: str | None = None,
    series_context: dict | None = None,
) -> dict[str, object]:
    """Draft, critique, and package a script with publishing metadata.

    The cheap MiniMax route runs three passes: a retention draft, a strict
    editor that rewrites what fails the retention checklist, and a metadata pass
    that produces titles, description, hashtags and a cover concept. Any
    failing pass degrades to the previous good state, never to an empty script.
    """
    fmt, niche_label = _resolve_format_and_niche(format, niche)
    is_long = fmt == "long"
    is_reel = fmt == "reel"
    budget, min_chars = _writer_budget(is_long, is_reel)
    script = build_script(
        topic,
        brand,
        format=fmt,
        niche=niche_label,
        custom_instruction=custom_instruction,
        genre=genre,
        pack_id=pack_id,
        series_context=series_context,
    )
    package: dict[str, object] = {
        "script": script,
        "metadata": {},
        "critic": {"applied": False},
    }
    try:
        from .provider_credentials import (
            get_provider_for_role,
            get_provider_secret,
            no_paid_providers_enabled,
            provider_cooldown_until,
        )

        provider = get_provider_for_role("script")
        if provider != "minimax" or no_paid_providers_enabled():
            return package
        if provider_cooldown_until(provider) is not None:
            return package
        api_key = minimax_text_key()
        if not api_key:
            return package
    except Exception:
        return package

    if script and len(script.strip()) >= min_chars:
        try:
            from .packs import get_pack, pack_directives

            pack = get_pack(pack_id)
            prompt = _script_prompt(
                topic,
                niche_label,
                budget,
                fmt,
                facts,
                custom_instruction,
                genre,
                pack_directive=pack_directives(pack),
                series_context=series_context,
            )
            review = _minimax_chat(
                api_key,
                CRITIC_SYSTEM,
                (
                    f"{CRITIC_RULES}\n\nRequired closing line (exact):\n{CLOSING_LINES}\n\n"
                    f"Topic: {topic}\nNiche: {niche_label}\nFormat: {fmt}\n\n"
                    "CANDIDATE SCRIPT:\n"
                    f"{script}"
                ),
                max_tokens=5000 if is_long else 2800,
                temperature=0.35,
            )
            if review and len(review.strip()) >= min_chars:
                package["script"] = review
                package["critic"] = {"applied": True}
        except Exception:
            logger.warning("Retention editor pass failed; keeping the valid draft", exc_info=True)

    final_script = _enforce_cliffhanger(str(package["script"]), series_context)
    package["script"] = final_script
    try:
        from .packs import get_pack

        pack = get_pack(pack_id)
        title_rule = pack.title_formula or "A clear, specific title."
        caption_rule = pack.caption_style or "Short, readable captions."
        series_note = ""
        if series_context and series_context.get("part_count"):
            series_note = (
                f"This is part {series_context.get('part_index')} of "
                f"{series_context.get('part_count')} of a story series. The title must "
                "stand on its own but hint that the story continues.\n"
            )
        metadata_prompt = (
            "Return ONLY compact JSON with these keys:\n"
            '{"title_variants": ["three punchy titles, each under 70 characters, no clickbait lies"],'
            ' "description": "two or three sentences with one natural keyword",'
            ' "hashtags": ["eight to twelve short hashtags"],'
            ' "hook_type": "stat|paradox|visual_reveal|myth|question",'
            ' "thumbnail_concept": "one concrete cover image idea, no text on the image"}\n\n'
            f"Topic: {topic}\nNiche: {niche_label}\nFormat: {fmt}\n"
            f"Title rule: {title_rule}\nCaption style: {caption_rule}\n{series_note}\n"
            f"NARRATION:\n{final_script[:2500]}"
        )
        payload = _parse_json_object(
            _minimax_chat(
                api_key,
                METADATA_SYSTEM,
                metadata_prompt,
                max_tokens=2400,
                temperature=0.45,
            )
        )
        titles = [
            " ".join(str(item).split())[:100]
            for item in (payload.get("title_variants") or [])
            if str(item).strip()
        ][:3]
        hashtags = [
            str(item).strip()[:40]
            for item in (payload.get("hashtags") or [])
            if str(item).strip()
        ][:12]
        pack_hashtags = [
            item for item in pack.hashtag_pool if item not in hashtags
        ]
        package["metadata"] = {
            "title_variants": titles,
            "description": " ".join(str(payload.get("description") or "").split())[:1200],
            "hashtags": (hashtags + pack_hashtags)[:12],
            "hook_type": str(payload.get("hook_type") or "")[:24],
            "thumbnail_concept": " ".join(str(payload.get("thumbnail_concept") or "").split())[:300],
        }
    except Exception:
        logger.warning("Metadata pass failed; using deterministic metadata", exc_info=True)
    hooks = build_hook_variants(final_script, topic, minimax_text_key() or api_key)
    if hooks:
        package["metadata"]["hook_variants"] = hooks
    return package


def build_hook_variants(
    script: str,
    topic: str,
    api_key: str,
    *,
    count: int = 3,
) -> list[str]:
    """Propose alternative opening lines for a finished script.

    This is a separate, cheap pass so a failure here never costs the titles,
    description or hashtags, and the owner can still swap the opening.
    """
    if not script.strip() or not api_key:
        return []
    prompt = (
        "Give exactly three alternative opening lines for a short vertical video.\n"
        "Return ONLY a JSON array of three strings.\n"
        "Each string is one punchy sentence in the same language as the script, "
        "under twelve words, that hooks the viewer and promises the payoff.\n"
        "Do not number them and do not explain them.\n\n"
        f"Topic: {topic}\n\n"
        f"SCRIPT:\n{script[:1200]}"
    )
    try:
        raw = _minimax_chat(
            api_key,
            METADATA_SYSTEM,
            prompt,
            max_tokens=1600,
            temperature=0.8,
        )
        variants = _parse_json_array(raw)
    except Exception:
        logger.warning("Hook variant pass failed; keeping the current opening", exc_info=True)
        return []
    cleaned: list[str] = []
    for variant in variants:
        text = " ".join(str(variant).split())[:220]
        if len(text) < 8 or text in cleaned:
            continue
        cleaned.append(text)
    return cleaned[:count]

def research_topic_angle(topic: str, niche: str | None = None) -> dict[str, object]:
    """Ask the cheap research route for a hookable angle on a topic.

    Only widely known, uncontroversial points are requested. When the provider
    is unavailable or the answer is unusable, an empty result is returned so
    automation keeps its deterministic trend ordering.
    """
    try:
        from .provider_credentials import (
            get_provider_for_role,
            get_provider_secret,
            no_paid_providers_enabled,
            provider_cooldown_until,
        )

        provider = get_provider_for_role("script")
        if provider != "minimax" or no_paid_providers_enabled():
            return {}
        if provider_cooldown_until(provider) is not None:
            return {}
        api_key = minimax_text_key()
        if not api_key:
            return {}
    except Exception:
        return {}

    prompt = (
        "Return ONLY compact JSON with these keys:\n"
        '{"angle": "one specific hookable angle for this topic in Hindi",'
        ' "opening_line": "the exact first sentence to say, in Devanagari",'
        ' "claims": ["three to five uncontroversial points to develop"],'
        ' "why_now": "one short line on why this matters now"}\n\n'
        "Rules: use only widely known, uncontroversial facts. Never invent "
        "statistics, quotes, dates or studies. If you are unsure about a point, "
        "leave it out.\n\n"
        f"Topic: {topic}\nChannel niche: {niche or 'Hindi educational facts explainer'}"
    )
    try:
        payload = _parse_json_object(
            _minimax_chat(
                api_key,
                RESEARCH_SYSTEM,
                prompt,
                max_tokens=2000,
                temperature=0.4,
            )
        )
    except Exception:
        logger.debug("Angle research failed for %s", topic, exc_info=True)
        return {}
    return {
        "angle": " ".join(str(payload.get("angle") or "").split())[:400],
        "opening_line": " ".join(str(payload.get("opening_line") or "").split())[:400],
        "claims": [
            " ".join(str(item).split())[:300]
            for item in (payload.get("claims") or [])
            if str(item).strip()
        ][:6],
        "why_now": " ".join(str(payload.get("why_now") or "").split())[:300],
    }
