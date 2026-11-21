import json
from unittest.mock import patch

import pytest


def test_script_draft_stops_before_media_spend():
    from app.services import create_job, generate_content

    job = create_job("नई AI तकनीक का असर", "youtube")
    with (
        patch("app.services.build_script", return_value=("क्या यह बदलाव सच में उपयोगी है? " * 20)),
        patch("app.services.create_job_assets") as create_assets,
    ):
        drafted = generate_content(job.id)

    assert drafted.status == "awaiting_script_approval"
    assert drafted.stage == "script"
    metadata = json.loads(drafted.metadata_json)
    assert metadata["script_approval_version"] is None
    assert metadata["assets"] == {}
    create_assets.assert_not_called()


def test_approved_script_generates_assets_once():
    from app.services import approve_script, create_job, generate_assets, generate_content

    job = create_job("भारत की नई अंतरिक्ष तकनीक", "youtube")
    with patch("app.services.build_script", return_value=("क्या यह मिशन इतिहास बदल देगा? " * 20)):
        drafted = generate_content(job.id)
    approved = approve_script(drafted.id, 12345, drafted.content_version)
    assert approved.status == "queued"
    assert approved.stage == "assets"

    with patch(
        "app.services.create_job_assets",
        return_value={"voiceover": f"{job.id}-voiceover.mp3"},
    ) as create_assets:
        generated = generate_assets(job.id, drafted.content_version)

    assert generated.status == "queued"
    assert generated.stage == "render"
    metadata = json.loads(generated.metadata_json)
    assert metadata["script_approval_version"] == drafted.content_version
    assert metadata["assets_version"] == drafted.content_version
    assert metadata["assets"]["voiceover"].endswith("-voiceover.mp3")
    create_assets.assert_called_once()

    with pytest.raises(ValueError, match="not available"):
        generate_assets(job.id, drafted.content_version)


def test_stale_script_approval_is_rejected():
    from app.services import approve_script, create_job, generate_content, revise_job

    job = create_job("क्वांटम कंप्यूटर कैसे काम करता है", "youtube")
    with patch("app.services.build_script", return_value=("क्या कंप्यूटर का भविष्य बदल चुका है? " * 20)):
        drafted = generate_content(job.id)
    revise_job(job.id, drafted.content_version)

    with pytest.raises(ValueError, match="stale"):
        approve_script(job.id, 12345, drafted.content_version)


def test_final_approval_callback_version_is_checked():
    from app.db import ContentJob, SessionLocal
    from app.services import approve_job, create_job

    job = create_job("Approval version test", "youtube")
    with SessionLocal() as session:
        stored = session.get(ContentJob, job.id)
        stored.status = "awaiting_approval"
        stored.stage = "quality_check"
        stored.content_version = 2
        session.commit()

    with pytest.raises(ValueError, match="stale"):
        approve_job(job.id, 12345, expected_version=1)
