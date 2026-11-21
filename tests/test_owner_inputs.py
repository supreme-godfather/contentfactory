import json
from pathlib import Path

import pytest


def test_owner_can_replace_the_generated_script():
    from app.db import ContentJob, SessionLocal
    from app.job_inputs import set_custom_script
    from app.services import create_job, generate_content

    job = create_job("क्वांटम कंप्यूटर", "youtube", request_id="custom-script")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("app.services.build_script", lambda *args, **kwargs: "क्या यह सच है? " * 20)
        drafted = generate_content(job.id)

    revised = set_custom_script(
        job.id,
        drafted.content_version,
        "मेरा अपना स्क्रिप्ट है जो मैंने खुद लिखा है और यह काफी लंबा है ताकि यह मान्य हो।",
    )

    assert revised.content_version == drafted.content_version + 1
    assert revised.status == "awaiting_script_approval"
    assert revised.stage == "script"
    assert "मेरा अपना स्क्रिप्ट" in revised.script_text
    metadata = json.loads(revised.metadata_json)
    assert metadata["custom_script"] is True
    assert metadata["script_approval_version"] is None
    assert metadata["caption_cues"]


def test_owner_voiceover_is_stored_and_used_instead_of_provider_tts(tmp_path, monkeypatch):
    from app.owner_inputs import save_custom_voice

    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path))
    name = save_custom_voice(tmp_path, "job-1", b"ID3" + b"a" * 512)
    assert name == "job-1-custom-voice.mp3"
    assert (tmp_path / name).is_file()

    with pytest.raises(ValueError):
        save_custom_voice(tmp_path, "job-1", b"not audio at all")


def test_owner_cover_must_be_a_jpeg(tmp_path):
    from app.owner_inputs import save_custom_cover

    name = save_custom_cover(tmp_path, "job-1", "instagram", b"\xff\xd8\xff" + b"b" * 2048)
    assert name == "job-1-cover-instagram.jpg"
    with pytest.raises(ValueError):
        save_custom_cover(tmp_path, "job-1", "instagram", b"PNG data")


def test_watermark_settings_are_runtime_controlled():
    from app.hq import (
        get_watermark_text,
        is_watermark_enabled,
        set_watermark_enabled,
        set_watermark_text,
    )

    assert is_watermark_enabled() is True
    assert get_watermark_text() == "@sheoran_studios"
    set_watermark_enabled(False)
    assert is_watermark_enabled() is False
    set_watermark_enabled(True)
    set_watermark_text("@mychannel")
    assert get_watermark_text() == "@mychannel"


def test_worker_burns_watermark_into_top_right(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import patch
    from worker.worker import render_video

    def fake_run(command, **_kwargs):
        Path(command[-1]).write_bytes(b"x" * 2048)
        return SimpleNamespace(returncode=0, stderr="")

    with patch("worker.worker.shutil.which", side_effect=["ffmpeg", "ffprobe"]), patch(
        "worker.worker._audio_duration", return_value=None
    ), patch("worker.worker.subprocess.run", side_effect=fake_run) as run:
        render_video(
            {
                "id": "job-id",
                "topic": "topic",
                "script": "script",
                "duration_seconds": 20,
                "caption_cues": [{"text": "text", "start_seconds": 0, "end_seconds": 20}],
                "watermark": {"enabled": True, "text": "@sheoran_studios"},
            },
            tmp_path,
        )

    scene_command = next(call.args[0] for call in run.call_args_list if "-vf" in call.args[0])
    video_filter = scene_command[scene_command.index("-vf") + 1]
    assert "drawtext" in video_filter
    assert "x=w-tw-22:y=22" in video_filter
    assert "fontcolor=white@0.85" in video_filter
    assert (tmp_path / "job-id-watermark.txt").read_text(encoding="utf-8") == "@sheoran_studios"


def test_script_package_uses_editor_pass_and_metadata():
    from unittest.mock import patch
    from app.scriptwriter import build_script_package

    draft = "यह पहला मसौदा है जो काफी लंबा है ताकि यह मान्य हो जाए। " * 8
    polished = "यह सुधरा हुआ मसौदा है जो काफी लंबा है ताकि यह मान्य हो जाए। " * 8
    metadata_json = json.dumps({
        "title_variants": ["पहला टाइटल", "दूसरा टाइटल", "तीसरा टाइटल"],
        "description": "एक अच्छा विवरण जो दर्शकों को रोकता है।",
        "hashtags": ["#Hindi", "#Facts"],
        "hook_type": "stat",
        "thumbnail_concept": "एक चौंकाने वाला चेहरा",
    })

    with (
        patch("app.provider_credentials.get_provider_for_role", return_value="minimax"),
        patch("app.provider_credentials.get_provider_secret", return_value="key"),
        patch("app.provider_credentials.no_paid_providers_enabled", return_value=False),
        patch("app.provider_credentials.provider_cooldown_until", return_value=None),
        patch("app.scriptwriter._minimax_chat", side_effect=[draft, polished, metadata_json]),
    ):
        package = build_script_package("एक विषय", format="reel", niche="Hindi facts")

    assert package["script"] == polished
    assert package["critic"]["applied"] is True
    assert package["metadata"]["title_variants"][0] == "पहला टाइटल"
    assert package["metadata"]["hook_type"] == "stat"


def test_research_angle_returns_empty_without_a_usable_provider():
    from unittest.mock import patch
    from app.scriptwriter import research_topic_angle

    with (
        patch("app.provider_credentials.get_provider_for_role", return_value="minimax"),
        patch("app.provider_credentials.get_provider_secret", return_value="key"),
        patch("app.provider_credentials.no_paid_providers_enabled", return_value=True),
    ):
        assert research_topic_angle("कोई विषय") == {}
