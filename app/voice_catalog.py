from dataclasses import asdict, dataclass

import httpx

from .config import get_settings
from .provider_credentials import get_provider_secret

FISH_OFFICIAL_AUTHOR_ID = "d8b0991f96b44e489422ca2ddf0bd31d"
FISH_HINDI_SEEDS = (
    ("4ebf5f78ef984caba1fbf70c8803f12b", "Aryan", "Clear, friendly Hindi explainer/tutor"),
    ("28dcd01c14684153842b6d0b4713af80", "Sanjay", "Authoritative, warm Hindi narrator"),
    ("6cda55186a3a4c92ae612a78ec8d611e", "Nisha", "Energetic Hindi announcer"),
    ("481dc265822c418cb07b0cacc88e2879", "Kalpana", "Calm, measured Hindi narrator"),
)
MINIMAX_HINDI_SYSTEM_VOICES = (
    ("hindi_male_1_v2", "Trustworthy Advisor", "Male Hindi system voice"),
    ("hindi_female_2_v1", "Tranquil Woman", "Calm female Hindi system voice"),
    ("hindi_female_1_v2", "News Anchor", "Female Hindi news system voice"),
)


@dataclass(frozen=True)
class VoiceOption:
    provider: str
    voice_id: str
    name: str
    description: str
    source: str
    preview_url: str = ""
    owned: bool = False
    licensed: bool = False


def _safe_option(option: VoiceOption) -> dict[str, object]:
    return asdict(option)


def discover_fish_hindi_voices() -> list[dict[str, object]]:
    api_key = get_provider_secret("fish_audio")
    if not api_key:
        return [
            _safe_option(VoiceOption("fish_audio", voice_id, name, description, "Fish Official"))
            for voice_id, name, description in FISH_HINDI_SEEDS
        ]
    response = httpx.get(
        "https://api.fish.audio/model",
        headers={"Authorization": f"Bearer {api_key}"},
        params={
            "language": "hi",
            "author_id": FISH_OFFICIAL_AUTHOR_ID,
            "page_size": 100,
        },
        timeout=30,
    )
    response.raise_for_status()
    options = []
    for item in response.json().get("items") or []:
        author = item.get("author") or {}
        if (
            item.get("state") != "trained"
            or item.get("dmca_taken_down")
            or author.get("_id") != FISH_OFFICIAL_AUTHOR_ID
            or "hi" not in (item.get("languages") or [])
        ):
            continue
        samples = item.get("samples") or []
        preview_url = str(samples[0].get("audio") or "") if samples else ""
        options.append(_safe_option(VoiceOption(
            provider="fish_audio",
            voice_id=str(item.get("_id") or ""),
            name=str(item.get("title") or "Fish Hindi voice")[:100],
            description=str(item.get("description") or "")[:300],
            source="Fish Official",
            preview_url=preview_url,
            licensed=bool(item.get("licensed")),
        )))
    return options or [
        _safe_option(VoiceOption("fish_audio", voice_id, name, description, "Fish Official"))
        for voice_id, name, description in FISH_HINDI_SEEDS
    ]


def discover_minimax_hindi_voices() -> list[dict[str, object]]:
    settings = get_settings()
    api_key = settings.minimax_api_key or get_provider_secret("minimax")
    seeds = {
        voice_id: VoiceOption("minimax", voice_id, name, description, "MiniMax system")
        for voice_id, name, description in MINIMAX_HINDI_SYSTEM_VOICES
    }
    if not api_key:
        return [_safe_option(option) for option in seeds.values()]
    response = httpx.post(
        "https://api.minimax.io/v1/get_voice",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"voice_type": "system"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    base_response = payload.get("base_resp") or {}
    if int(base_response.get("status_code") or 0) != 0:
        raise RuntimeError("MiniMax voice catalog request was rejected.")
    available = {str(item.get("voice_id")) for item in payload.get("system_voice") or []}
    validated = [option for voice_id, option in seeds.items() if voice_id in available]
    return [_safe_option(option) for option in (validated or list(seeds.values()))]


def discover_elevenlabs_hindi_voices(search: str = "") -> list[dict[str, object]]:
    api_key = get_provider_secret("elevenlabs")
    if not api_key:
        raise ValueError("Add the ElevenLabs API key before browsing voices.")
    params: list[tuple[str, str | int | bool]] = [
        ("page_size", 50),
        ("language", "hi"),
        ("sort", "name"),
        ("sort_direction", "asc"),
        ("include_total_count", False),
    ]
    if search.strip():
        params.append(("search", search.strip()[:80]))
    response = httpx.get(
        "https://api.elevenlabs.io/v2/voices",
        headers={"xi-api-key": api_key},
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    options = []
    for item in response.json().get("voices") or []:
        voice_id = str(item.get("voice_id") or "")
        if not voice_id:
            continue
        labels = item.get("labels") or {}
        verified = item.get("verified_languages") or []
        hindi_verified = any(
            str(language.get("language") or "").lower() in {"hi", "hindi"}
            or str(language.get("locale") or "").lower().startswith("hi")
            for language in verified
            if isinstance(language, dict)
        )
        label_language = str(labels.get("language") or "").lower()
        if not hindi_verified and label_language not in {"hi", "hindi"}:
            continue
        preview_url = str(item.get("preview_url") or "")
        if not preview_url and verified:
            preview_url = str((verified[0] or {}).get("preview_url") or "")
        options.append(_safe_option(VoiceOption(
            provider="elevenlabs",
            voice_id=voice_id,
            name=str(item.get("name") or "ElevenLabs Hindi voice")[:100],
            description=str(item.get("description") or "")[:300],
            source=("Owned" if item.get("is_owner") else str(item.get("category") or "Account voice")),
            preview_url=preview_url,
            owned=bool(item.get("is_owner")),
        )))
    return options


def discover_hindi_voices(provider: str) -> list[dict[str, object]]:
    if provider == "fish_audio":
        return discover_fish_hindi_voices()
    if provider == "minimax":
        return discover_minimax_hindi_voices()
    if provider == "elevenlabs":
        return discover_elevenlabs_hindi_voices()
    raise ValueError("Unsupported voice catalog provider")
