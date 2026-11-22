import json

import httpx
import pytest


def _response(status_code: int, payload: dict | None = None, content: bytes = b"") -> httpx.Response:
    request = httpx.Request("POST", "https://api.higgsfield.ai/test")
    if payload is not None:
        return httpx.Response(status_code, json=payload, request=request)
    return httpx.Response(status_code, content=content, request=request)


def test_credentials_must_use_the_key_id_format(monkeypatch):
    from app import higgsfield

    monkeypatch.setattr(
        "app.provider_credentials.get_provider_secret", lambda _name: "no-colon-here"
    )

    with pytest.raises(higgsfield.HiggsfieldError):
        higgsfield._credentials()


def test_credentials_are_read_from_the_provider_panel(monkeypatch):
    from app import higgsfield

    monkeypatch.setattr(
        "app.provider_credentials.get_provider_secret", lambda _name: "key-id:key-secret"
    )

    assert higgsfield._credentials() == ("key-id", "key-secret")


def test_public_media_url_needs_a_public_base_url(monkeypatch):
    from app import higgsfield

    monkeypatch.setattr(
        higgsfield,
        "get_settings",
        lambda: type("S", (), {"public_base_url": "https://factory.example"})(),
    )

    assert (
        higgsfield.public_media_url("character.jpg")
        == "https://factory.example/media/character.jpg"
    )


def test_public_media_url_rejects_a_path(monkeypatch):
    from app import higgsfield

    monkeypatch.setattr(
        higgsfield,
        "get_settings",
        lambda: type("S", (), {"public_base_url": "https://factory.example"})(),
    )

    assert higgsfield.public_media_url("../secret.jpg") is None


def test_credit_cost_scales_with_resolution_and_length():
    from app import higgsfield

    base = higgsfield.credit_cost(higgsfield.SEEDANCE_REFERENCE, resolution="720p", seconds=5)
    higher = higgsfield.credit_cost(
        higgsfield.SEEDANCE_REFERENCE, resolution="1080p", seconds=10
    )

    assert base == 23
    assert higher > base


def test_seedance_reference_needs_an_image_or_video(monkeypatch, tmp_path):
    from app import higgsfield

    with pytest.raises(higgsfield.HiggsfieldError):
        higgsfield.seedance_reference_video("a prompt", tmp_path / "out.mp4")


def test_generate_video_polls_and_downloads(monkeypatch, tmp_path):
    from app import higgsfield

    monkeypatch.setattr(higgsfield, "_headers", lambda: {"Authorization": "Key a:b"})
    monkeypatch.setattr(higgsfield, "_submit", lambda _endpoint, _payload: "https://status")

    def fake_get(url, headers=None, timeout=None, **_kwargs):
        if url == "https://status":
            return _response(200, {"status": "completed", "video": {"url": "https://file/out.mp4"}})
        return _response(200, content=b"video-bytes")

    monkeypatch.setattr(higgsfield.httpx, "get", fake_get)

    destination = tmp_path / "scene.mp4"
    credits = higgsfield.generate_video(
        higgsfield.SEEDANCE_REFERENCE, {"prompt": "x", "duration": 5}, destination
    )

    assert destination.read_bytes() == b"video-bytes"
    assert credits == 23


def test_failed_request_raises(monkeypatch, tmp_path):
    from app import higgsfield

    monkeypatch.setattr(higgsfield, "_headers", lambda: {"Authorization": "Key a:b"})
    monkeypatch.setattr(higgsfield, "_submit", lambda _endpoint, _payload: "https://status")
    monkeypatch.setattr(
        higgsfield.httpx,
        "get",
        lambda url, headers=None, timeout=None: _response(200, {"status": "failed"}),
    )

    with pytest.raises(higgsfield.HiggsfieldError):
        higgsfield.generate_video(
            higgsfield.SEEDANCE_REFERENCE, {"prompt": "x"}, tmp_path / "scene.mp4"
        )


def test_seedance_payload_carries_the_references(monkeypatch, tmp_path):
    from app import higgsfield

    captured: dict = {}

    def fake_generate(endpoint, payload, destination, *, timeout_seconds=900):
        captured["endpoint"] = endpoint
        captured["payload"] = payload
        return 23

    monkeypatch.setattr(higgsfield, "generate_video", fake_generate)

    higgsfield.seedance_reference_video(
        "a boss enters",
        tmp_path / "out.mp4",
        image_urls=["https://factory.example/media/character.jpg"],
        audio_urls=["https://factory.example/media/line.mp3"],
        duration=5,
        aspect_ratio="9:16",
    )

    assert captured["endpoint"] == higgsfield.SEEDANCE_REFERENCE
    assert captured["payload"]["image_urls"] == ["https://factory.example/media/character.jpg"]
    assert captured["payload"]["audio_urls"] == ["https://factory.example/media/line.mp3"]
    assert captured["payload"]["aspect_ratio"] == "9:16"


def test_credit_vault_tracks_spend(monkeypatch):
    from app.credits import (
        credit_summary,
        credits_spent,
        remaining_balance,
        set_starting_balance,
    )
    from app.cost import record_usage

    set_starting_balance(15000)

    record_usage("job-1", "higgsfield", "credit", 23, "credit", 0.0143)

    assert credits_spent() == 23
    assert remaining_balance() == 15000 - 23
    assert credit_summary()["spent"] == 23


def test_series_credit_cap_stops_the_series(monkeypatch):
    from app.credits import enforce_series_credit_budget, set_series_cap
    from app.cost import record_usage
    from app.db import ContentJob, SessionLocal, init_db

    init_db()
    with SessionLocal() as session:
        job = ContentJob(
            topic="Series reel",
            platform="instagram",
            status="queued",
            idempotency_key="credit-cap-job",
            series_id="series-1",
        )
        session.add(job)
        session.commit()
        job_id = job.id

    set_series_cap(40)
    record_usage(job_id, "higgsfield", "credit", 45, "credit", 0.0143)

    with pytest.raises(ValueError):
        enforce_series_credit_budget("series-1")
