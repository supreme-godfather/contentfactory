import json
import secrets
import time
from hashlib import sha256
from pathlib import Path
from random import SystemRandom

from .provider_credentials import (
    get_elevenlabs_voice_id,
    get_fish_audio_reference_id,
    get_minimax_voice_id,
    no_paid_providers_enabled,
    provider_status,
)

DEFAULT_VOICE_BENCHMARK_TEXT = (
    "आज की सबसे बड़ी टेक्नोलॉजी कहानी सिर्फ एक नया फीचर नहीं है। "
    "असली सवाल यह है कि क्या यह बदलाव हमारे काम को तेज करेगा, या हमारी आदतों को नियंत्रित करेगा? "
    "ध्यान दीजिए: आंकड़े, अंग्रेज़ी शब्द, और भावनात्मक ठहराव तभी असर करते हैं जब आवाज़ इंसान जैसी लगे। "
    "अगले कुछ सेकंड में हम इसी फर्क को साफ़ सुनेंगे। आपकी नज़र में कौन-सी आवाज़ सबसे विश्वसनीय है?"
)


def _ready_providers() -> list[str]:
    status = provider_status()
    ready = []
    if status.get("fish_audio") and get_fish_audio_reference_id():
        ready.append("fish_audio")
    if status.get("elevenlabs") and get_elevenlabs_voice_id():
        ready.append("elevenlabs")
    if status.get("minimax") and get_minimax_voice_id():
        ready.append("minimax")
    return ready


def _generator(provider: str):
    if provider == "elevenlabs":
        from .google_media import generate_elevenlabs_voiceover

        return generate_elevenlabs_voiceover
    if provider == "fish_audio":
        from .voice_providers import generate_fish_audio_voiceover

        return generate_fish_audio_voiceover
    if provider == "minimax":
        from .voice_providers import generate_minimax_voiceover

        return generate_minimax_voiceover
    raise ValueError("Unsupported benchmark provider")


def run_voice_benchmark(
    media_root: Path,
    *,
    text: str = DEFAULT_VOICE_BENCHMARK_TEXT,
    providers: list[str] | None = None,
) -> dict[str, object]:
    if no_paid_providers_enabled():
        raise ValueError("Disable no-paid mode explicitly before running a voice benchmark.")
    selected = providers or _ready_providers()
    selected = list(dict.fromkeys(selected))
    if len(selected) < 2:
        raise ValueError("Configure at least two benchmark voice providers first.")
    unsupported = set(selected) - {"fish_audio", "elevenlabs", "minimax"}
    if unsupported:
        raise ValueError("Unsupported benchmark provider")

    benchmark_id = secrets.token_hex(12)
    output_dir = media_root / "benchmarks" / benchmark_id
    output_dir.mkdir(parents=True, exist_ok=False)
    labels = [chr(ord("A") + index) for index in range(len(selected))]
    SystemRandom().shuffle(selected)

    samples: list[dict[str, object]] = []
    answer_key: dict[str, str] = {}
    for label, provider in zip(labels, selected, strict=True):
        answer_key[label] = provider
        destination = output_dir / f"voice-{label.lower()}.mp3"
        started = time.perf_counter()
        try:
            _generator(provider)(text, destination)
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            data = destination.read_bytes()
            samples.append(
                {
                    "label": label,
                    "filename": destination.name,
                    "latency_ms": elapsed_ms,
                    "bytes": len(data),
                    "sha256": sha256(data).hexdigest(),
                    "status": "ready",
                }
            )
        except Exception as exc:
            destination.unlink(missing_ok=True)
            samples.append(
                {
                    "label": label,
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                    "status": "failed",
                    "error_type": type(exc).__name__,
                }
            )

    if sum(sample["status"] == "ready" for sample in samples) < 2:
        raise RuntimeError("Fewer than two voice providers completed the benchmark.")

    public_manifest = {
        "benchmark_id": benchmark_id,
        "text": text,
        "samples": samples,
    }
    (output_dir / "results.json").write_text(
        json.dumps(public_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "answer-key.json").write_text(
        json.dumps(answer_key, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    return {**public_manifest, "output_dir": str(output_dir)}


def reveal_voice_benchmark(media_root: Path, benchmark_id: str) -> dict[str, str]:
    if not benchmark_id.isalnum() or len(benchmark_id) != 24:
        raise ValueError("Invalid voice benchmark ID")
    benchmark_root = (media_root / "benchmarks").resolve()
    answer_path = (benchmark_root / benchmark_id / "answer-key.json").resolve()
    if answer_path.parent.parent != benchmark_root or not answer_path.is_file():
        raise ValueError("Voice benchmark was not found")
    payload = json.loads(answer_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in payload.items()
    ):
        raise ValueError("Voice benchmark answer key is invalid")
    return payload
