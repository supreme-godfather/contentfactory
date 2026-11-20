"""AI scriptwriter with a safe deterministic Hindi template fallback.

Scripts follow the retention arc used across the factory (see
app/storyboard.py): golden hook -> disrupt the assumption -> insider facts
-> ultimate truth -> discussion question -> fixed closing line.

The current **niche** and **default format** come from the Telegram
headquarters settings (app/hq.py): Shorts use a ~130-150 word budget
(~60 seconds), long-form uses ~550-700 words (~4 minutes) with a deeper
chapter structure.

Never raises: any provider error, missing key or short reply falls back to
the built-in template that follows the same structure.
"""
import logging

import httpx

logger = logging.getLogger(__name__)

CLOSING_LINES = (
    "ऐसी और कहानियों के लिए Subscribe करें और Instagram पर Follow करना न भूलें।"
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
        f"सबसे बड़ी सीख यह है कि {topic} को समझना सिर्फ़ जानकारी नहीं है — "
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
        "सिर्फ़ नतीजा देखते हैं, लेकिन असली सीख उस प्रक्रिया में छिपी होती है जो "
        "नतीजे तक पहुँचाती है। यही वजह है कि जो लोग प्रक्रिया समझते हैं, वे दूसरों "
        "से हमेशा एक कदम आगे रहते हैं।\n\n"
        f"अब आते हैं पहले गहरे तथ्य पर। {topic} का सबसे महत्वपूर्ण पहलू वह है जिसे "
        "अधिकतर लोग नज़रअंदाज़ कर देते हैं — इसका सीधा असर। जब यह विषय सही तरीके "
        "से काम करता है, तो इसका प्रभाव सिर्फ़ एक जगह नहीं, बल्कि कई जगहों पर एक "
        "साथ दिखता है। और जब यह गड़बड़ होता है, तो नतीजे भी उतने ही साफ़ दिखते "
        "हैं। इसीलिए विशेषज्ञ हमेशा कहते हैं कि सतह नहीं, व्यवस्था को समझो।\n\n"
        f"दूसरा तथ्य और भी दिलचस्प है। {topic} में जो चीज़ बाहर से सबसे सरल दिखती "
        "है, अक्सर वही अंदर से सबसे जटिल होती है। इसमें कई छोटे हिस्से एक साथ काम "
        "करते हैं, और हर हिस्सा दूसरे पर निर्भर होता है। एक हिस्सा बदला, तो पूरा "
        "संतुलन बदल जाता है। यही परस्पर निर्भरता इसे इतना मज़बूत भी बनाती है और "
        "इतना नाज़ुक भी।\n\n"
        f"तीसरा तथ्य आपके रोज़ के जीवन से जुड़ा है। {topic} का असर सिर्फ़ किताबों "
        "या ख़बरों में नहीं मिलता — यह आपके रोज़ के फ़ैसलों, आपकी आदतों और कभी-कभी "
        "आपके ख़र्च तक में दिखता है। जो लोग इस संबंध को जल्दी पहचान लेते हैं, वे "
        "बेहतर सवाल पूछते हैं, और बेहतर सवाल ही बेहतर जवाबों की शुरुआत होते हैं।\n\n"
        f"इसे एक आसान उदाहरण से समझिए। कल्पना कीजिए दो लोग {topic} को देखते हैं — "
        "एक सिर्फ़ नतीजा देखता है, दूसरा वजह भी। पहला हर बार हैरान होता है, दूसरा "
        "पहले से तैयार रहता है। फ़र्क़ ज्ञान का नहीं, नज़रिए का है। और अच्छी बात "
        "यह है कि नज़रिया सीखा जा सकता है — बिल्कुल वैसे, जैसे आप इस वीडियो में "
        "सीख रहे हैं।\n\n"
        f"अब एक आम ग़लतफ़हमी दूर कर देते हैं। कई लोग मानते हैं कि {topic} में सफलता "
        "या समझ सिर्फ़ किस्मत या प्रतिभा से आती है। यह आधा सच है। असली तस्वीर यह "
        "है कि लगातार ध्यान, सही जानकारी और धैर्य — ये तीनों मिलकर वह नतीजा बनाते "
        "हैं जिसे बाहर से किस्मत कहा जाता है।\n\n"
        f"तो सबसे बड़ी सीख क्या है? {topic} को समझना सिर्फ़ जानकारी इकट्ठा करना "
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


def build_script(
    topic: str,
    brand: str = "learn_with_elite_explainer",
    format: str | None = None,
    niche: str | None = None,
    custom_instruction: str | None = None,
) -> str:
    fmt, niche_label = _resolve_format_and_niche(format, niche)
    is_long = fmt == "long"
    is_reel = fmt == "reel"
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
            get_provider_secret("minimax_mplan")
            if provider == "minimax"
            else get_provider_secret(provider)
        )
        if not api_key:
            return fallback
    except Exception:
        logger.debug("Provider lookup failed; using template script", exc_info=True)
        return fallback

    if is_long:
        budget = (
            "550-700 words, about 4 minutes of narration, organised in chapters: "
            "background, three deep facts (one chapter each), one worked example, "
            "one myth-versus-fact section, and a summary"
        )
        min_chars = 1500
    elif is_reel:
        budget = "105-140 words, about 45-60 seconds of narration, written for a dark cinematic Reel"
        min_chars = 80
    else:
        budget = "130-150 words, about 60 seconds of narration"
        min_chars = 80
    prompt = (
        f"Write an original Hindi video script ({budget}) for a channel whose niche is: "
        f"{niche_label}. Topic: {topic}. Follow this exact retention structure:\n"
        "1) Golden hook: open with a surprising question in the first line (first 3 seconds).\n"
        "2) Disrupt the assumption: say what most people wrongly think, then that the truth is different.\n"
        "3) Insider facts: explain the main facts and the real mechanism in simple Hindi, in concrete steps.\n"
        "4) Ultimate truth: end the explanation with one memorable lesson.\n"
        "5) Discussion CTA: ask the viewer one genuine question and invite them to answer in the comments.\n"
        + (
            "For this Reel, use short punchy Hindi lines, a mysterious documentary tone, "
            "and visual beats suitable for bold captions every 2-4 seconds.\n"
            if is_reel
            else ""
        )
        +
        "Rules: one core idea only; do not copy any existing creator, video or article; "
        "use only widely verifiable facts and never invent statistics, quotes or claims; "
        "spell numbers out as words (the script is read aloud by text-to-speech); "
        f"end with exactly these closing lines: '{CLOSING_LINES}'"
    )
    if custom_instruction:
        prompt += (
            "\n\nOwner-specific custom direction:\n"
            f"{' '.join(custom_instruction.split())[:1200]}\n"
            "Respect this direction while keeping the script original, factual and safe."
        )
    try:
        from .provider_credentials import get_provider_secret

        api_key = (
            get_provider_secret("minimax_mplan")
            if provider == "minimax"
            else get_provider_secret(provider)
        )
        if not api_key:
            return fallback
        if provider == "gemini":
            response = httpx.post(
                "https://generativelanguage.googleapis.com/v1beta/models/"
                "gemini-3.5-flash-lite:generateContent",
                headers={"x-goog-api-key": api_key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "systemInstruction": {
                        "parts": [{"text": "You write original Hindi explainer scripts."}]
                    },
                    "generationConfig": {"temperature": 0.7},
                },
                timeout=60,
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
                        {"role": "system", "content": "You write original Hindi explainer scripts."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.7,
                    "max_tokens": 1800 if is_long else 700,
                },
                timeout=90,
            )
        elif provider == "minimax":
            response = httpx.post(
                "https://api.minimax.io/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "MiniMax-M3.1-Flash",
                    "messages": [
                        {"role": "system", "content": "You write original Hindi explainer scripts and research briefs."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.7,
                    "max_tokens": 1800 if is_long else 900,
                },
                timeout=90,
            )
        else:
            response = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": "You write original Hindi explainer scripts."},
                        {"role": "user", "content": prompt},
                    ],
                },
                timeout=60,
            )
        response.raise_for_status()
        if provider == "gemini":
            parts = (
                response.json()
                .get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [])
            )
            content = "\n".join(
                part["text"]
                for part in parts
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        else:
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
