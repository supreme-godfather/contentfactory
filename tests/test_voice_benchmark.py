import json
from pathlib import Path
from unittest.mock import patch

import pytest


def test_voice_benchmark_creates_blind_samples_and_private_answer_key(tmp_path):
    from app.voice_benchmark import reveal_voice_benchmark, run_voice_benchmark

    def generator(provider: str):
        def write(_text: str, destination: Path) -> None:
            destination.write_bytes(provider.encode("ascii") * 64)

        return write

    with (
        patch("app.voice_benchmark.no_paid_providers_enabled", return_value=False),
        patch("app.voice_benchmark._generator", side_effect=generator),
    ):
        result = run_voice_benchmark(
            tmp_path,
            providers=["fish_audio", "elevenlabs", "minimax"],
        )

    assert len(result["samples"]) == 3
    assert all(sample["status"] == "ready" for sample in result["samples"])
    assert all("provider" not in sample for sample in result["samples"])
    output_dir = Path(result["output_dir"])
    assert (output_dir / "results.json").is_file()
    answer_key = reveal_voice_benchmark(tmp_path, str(result["benchmark_id"]))
    assert set(answer_key.values()) == {"fish_audio", "elevenlabs", "minimax"}
    public = json.loads((output_dir / "results.json").read_text(encoding="utf-8"))
    assert "answer_key" not in public


def test_voice_benchmark_requires_explicit_paid_mode(tmp_path):
    from app.voice_benchmark import run_voice_benchmark

    with patch("app.voice_benchmark.no_paid_providers_enabled", return_value=True):
        with pytest.raises(ValueError, match="Disable no-paid mode"):
            run_voice_benchmark(tmp_path, providers=["fish_audio", "elevenlabs"])


def test_voice_benchmark_reveal_rejects_invalid_id(tmp_path):
    from app.voice_benchmark import reveal_voice_benchmark

    with pytest.raises(ValueError, match="Invalid voice benchmark ID"):
        reveal_voice_benchmark(tmp_path, "../secrets")
