from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


def test_clipper_url_requires_public_https_dns(monkeypatch):
    from clipper.worker import validate_source_url

    monkeypatch.setattr(
        "clipper.worker.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("93.184.216.34", 443))],
    )
    assert validate_source_url("https://video.example/watch?v=1") == "https://video.example/watch?v=1"

    monkeypatch.setattr(
        "clipper.worker.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("127.0.0.1", 443))],
    )
    with pytest.raises(ValueError, match="non-public"):
        validate_source_url("https://video.example/watch?v=1")


def test_vtt_parser_and_candidate_selection_are_deterministic(tmp_path):
    from clipper.worker import parse_vtt, select_candidates

    vtt = tmp_path / "source.en.vtt"
    vtt.write_text(
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:05.000\nWhy is this important?\n\n"
        "00:00:21.000 --> 00:00:25.000\nHere is the first answer!\n\n"
        "00:00:51.000 --> 00:00:56.000\nThe secret becomes clear.\n\n"
        "00:01:21.000 --> 00:01:27.000\nWhat happens next?\n",
        encoding="utf-8",
    )
    cues = parse_vtt(vtt)
    first = select_candidates(120000, cues)
    second = select_candidates(120000, cues)

    assert first == second
    assert {candidate["rank"] for candidate in first} == {1, 2, 3}
    chronological = sorted(first, key=lambda item: item["start_ms"])
    assert all(
        previous["end_ms"] <= current["start_ms"]
        for previous, current in zip(chronological, chronological[1:])
    )
    assert all(15000 <= candidate["end_ms"] - candidate["start_ms"] <= 60000 for candidate in first)


def test_transcription_words_are_grouped_into_readable_cues():
    from clipper.worker import transcription_segments_to_cues

    cues = transcription_segments_to_cues([
        {"text": "यह", "start": 0.0, "end": 0.3},
        {"text": "एक", "start": 0.31, "end": 0.6},
        {"text": "बहुत", "start": 0.61, "end": 0.9},
        {"text": "अच्छा", "start": 0.91, "end": 1.2},
        {"text": "परीक्षण", "start": 1.21, "end": 1.7},
    ])
    assert cues[0]["text"] == "यह एक बहुत अच्छा"
    assert cues[1]["text"] == "परीक्षण"
    assert cues[-1]["end_ms"] == 1700
    assert len(cues[0]["words"]) == 4


def test_audio_peak_gets_lead_in_and_changes_reason():
    from clipper.worker import select_candidates

    candidates = select_candidates(
        120000,
        [],
        [{"time_ms": 12000, "strength": 900, "kind": "audio_peak"}],
    )
    first_section = next(candidate for candidate in candidates if candidate["start_ms"] < 40000)
    assert first_section["start_ms"] == 6000
    assert first_section["reason"] == "audio_peak"
    assert first_section["score"] > 500


def test_candidate_ass_escapes_override_tags(tmp_path):
    from clipper.worker import write_candidate_ass

    path = tmp_path / "clip.ass"
    write_candidate_ass(
        {
            "start_ms": 10000,
            "end_ms": 40000,
            "title": "Title",
            "cues": [
                {"start_ms": 12000, "end_ms": 15000, "text": r"{bad} path\value"},
            ],
        },
        path,
    )
    content = path.read_text(encoding="utf-8")
    assert r"\{bad\}" in content
    assert r"path\\value" in content


def test_candidate_ass_uses_karaoke_for_word_timing(tmp_path):
    from clipper.worker import write_candidate_ass

    path = tmp_path / "karaoke.ass"
    write_candidate_ass(
        {
            "start_ms": 0,
            "end_ms": 20000,
            "title": "Title",
            "cues": [{
                "start_ms": 1000,
                "end_ms": 2000,
                "text": "hello world",
                "words": [
                    {"text": "hello", "start_ms": 1000, "end_ms": 1400},
                    {"text": "world", "start_ms": 1500, "end_ms": 2000},
                ],
            }],
        },
        path,
    )
    content = path.read_text(encoding="utf-8")
    assert r"{\kf40}hello" in content
    assert r"{\kf50}world" in content


def test_render_candidate_uses_argument_list_and_bounded_profile(tmp_path):
    from clipper.worker import render_candidate

    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    output = tmp_path / "candidate.mp4"
    candidate = {
        "start_ms": 10000,
        "end_ms": 40000,
        "title": "Useful moment",
        "cues": [],
    }

    def fake_run(command, **_kwargs):
        output.write_bytes(b"x" * 2048)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch("clipper.worker.shutil.which", return_value="ffmpeg"), patch(
        "clipper.worker._run", side_effect=fake_run
    ) as run:
        render_candidate(source, candidate, output)

    command = run.call_args.args[0]
    assert isinstance(command, list)
    assert "-filter_complex" in command
    assert "ass=filename=" in command[command.index("-filter_complex") + 1]
    assert "1080:1920" in command[command.index("-filter_complex") + 1]
    assert run.call_args.kwargs["timeout"] == 600


def test_short_source_cannot_fabricate_three_clips():
    from clipper.worker import select_candidates

    with pytest.raises(ValueError, match="insufficient_distinct_material"):
        select_candidates(44000, [])


def test_direct_downloader_revalidates_redirects_and_enforces_limit(tmp_path, monkeypatch):
    from clipper.worker import download_direct_media

    monkeypatch.setattr(
        "clipper.worker.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("93.184.216.34", 443))],
    )

    class StreamResponse:
        status_code = 200
        headers = {"content-type": "video/mp4"}

        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def raise_for_status(self): return None
        def iter_bytes(self, _size): return iter([b"x" * 1024, b"y" * 1024])

    class Client:
        def __init__(self, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def stream(self, *_args, **_kwargs): return StreamResponse()

    monkeypatch.setattr("clipper.worker.httpx.Client", Client)
    path = download_direct_media("https://video.example/source.mp4", tmp_path, 4096)
    assert path.read_bytes() == b"x" * 1024 + b"y" * 1024
