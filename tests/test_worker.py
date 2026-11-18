from pathlib import Path

import pytest


def test_scene_text_uses_script_beats():
    from worker.worker import _scene_text

    result = _scene_text({"topic": "बाइक", "script": "पहला भाग\n\nदूसरा भाग\nतीसरा भाग"})
    assert result == ["पहला भाग", "दूसरा भाग", "तीसरा भाग"]


def test_scene_text_falls_back_to_topic():
    from worker.worker import _scene_text

    assert _scene_text({"topic": "bike", "script": ""}) == ["bike"]


def test_filter_path_is_ffmpeg_compatible():
    from worker.worker import _filter_path

    value = _filter_path(Path(r"C:\work\captions.txt"))
    assert value == r"C\:/work/captions.txt"


def test_ffmpeg_error_detail_preserves_start_and_end():
    from worker.worker import _ffmpeg_error_detail

    detail = _ffmpeg_error_detail("start " + ("x" * 1200) + " end")
    assert detail.startswith("start ")
    assert detail.endswith(" end")
    assert len(detail) < 1020


def test_factory_url_accepts_railway_https():
    from worker.worker import _validate_factory_url

    assert _validate_factory_url(" https://factory.example/ ") == "https://factory.example"


def test_configured_worker_id_is_stable_and_validated(monkeypatch):
    from worker.worker import _configured_worker_id

    monkeypatch.setenv("WORKER_ID", "railway-renderer")
    assert _configured_worker_id() == "railway-renderer"
    with pytest.raises(ValueError, match="WORKER_ID"):
        _configured_worker_id("bad worker id")


def test_factory_url_allows_http_only_for_local_worker():
    from worker.worker import _validate_factory_url

    assert _validate_factory_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000"
    with pytest.raises(ValueError, match="must use HTTPS"):
        _validate_factory_url("http://factory.example")


@pytest.mark.parametrize(
    "value",
    [
        "https://user:password@factory.example",
        "https://factory.example/path?token=secret",
        "https://factory.example/#fragment",
    ],
)
def test_factory_url_rejects_embedded_credentials_or_suffixes(value):
    from worker.worker import _validate_factory_url

    with pytest.raises(ValueError):
        _validate_factory_url(value)


def test_caption_timing_uses_metadata_cues():
    from worker.worker import _caption_timing

    cues = [
        {"text": "पहली बात", "start_seconds": 0, "end_seconds": 2},
        {"text": "दूसरी बात", "start_seconds": 2, "end_seconds": 4},
    ]
    assert _caption_timing({"caption_cues": cues}, 60) == [
        ("पहली बात", 0.0, 30.0),
        ("दूसरी बात", 30.0, 60),
    ]


def test_caption_timing_falls_back_to_topic():
    from worker.worker import _caption_timing

    result = _caption_timing({"topic": "topic", "script": ""}, 55)
    assert result == [("topic", 0.0, 55.0)]


def test_render_video_uses_input_dimensions_for_caption_box(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import patch
    from worker.worker import render_video

    def fake_run(command, **_kwargs):
        Path(command[-1]).write_bytes(b"x" * 2048)
        return SimpleNamespace(returncode=0, stderr="")

    with patch("worker.worker.shutil.which", side_effect=["ffmpeg", None]), patch(
        "worker.worker.subprocess.run", side_effect=fake_run
    ) as run:
        render_video(
            {
                "id": "job-id",
                "topic": "Cybersecurity",
                "script": "Use unique passwords",
                "duration_seconds": 15,
                "caption_cues": [
                    {"text": "Use unique passwords", "start_seconds": 0, "end_seconds": 15}
                ],
            },
            tmp_path,
        )

    commands = [call.args[0] for call in run.call_args_list]
    scene_command = next(command for command in commands if "-vf" in command)
    video_filter = scene_command[scene_command.index("-vf") + 1]
    assert "drawbox=x=30:y=ih-300:w=iw-60:h=220" in video_filter
    assert "scale=780:1386" in video_filter
    assert scene_command[scene_command.index("-loglevel") + 1] == "error"
    assert "-nostats" in scene_command
    assert scene_command[scene_command.index("-filter_threads") + 1] == "1"
    assert scene_command[scene_command.index("-threads") + 1] == "1"
    assert scene_command[scene_command.index("-preset") + 1] == "ultrafast"
    assert len(list(tmp_path.glob("job-id-scene-*.png"))) == 4
    assert len(list(tmp_path.glob("job-id-segment-*.mp4"))) == 4
    concat_command = next(command for command in commands if "-f" in command and "concat" in command)
    assert concat_command[concat_command.index("-c:v") + 1] == "copy"
    assert "-an" in concat_command


def test_render_video_uses_all_six_generated_scene_images(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import patch
    from worker.worker import render_video

    scene_images = []
    for index in range(1, 7):
        image = tmp_path / f"scene-{index}.png"
        image.write_bytes(b"image")
        scene_images.append(image)

    def fake_run(command, **_kwargs):
        Path(command[-1]).write_bytes(b"x" * 2048)
        return SimpleNamespace(returncode=0, stderr="")

    with patch("worker.worker.shutil.which", side_effect=["ffmpeg", None]), patch(
        "worker.worker.subprocess.run", side_effect=fake_run
    ) as run:
        render_video(
            {"id": "job-id", "topic": "topic", "script": "", "duration_seconds": 30},
            tmp_path,
            scene_images=scene_images,
        )

    scene_commands = [
        call.args[0]
        for call in run.call_args_list
        if "-vf" in call.args[0]
    ]
    assert len(scene_commands) == 6
    assert [
        Path(command[command.index("-i") + 1])
        for command in scene_commands
    ] == scene_images


def test_render_video_rejects_incomplete_generated_scene_set(tmp_path):
    from unittest.mock import patch
    from worker.worker import render_video

    scene_images = []
    for index in range(5):
        image = tmp_path / f"scene-{index}.png"
        image.write_bytes(b"image")
        scene_images.append(image)

    with patch("worker.worker.shutil.which", side_effect=["ffmpeg", None]):
        with pytest.raises(RuntimeError, match="All six generated scene images"):
            render_video(
                {"id": "job-id", "topic": "topic", "script": "", "duration_seconds": 30},
                tmp_path,
                scene_images=scene_images,
            )


def test_render_video_reports_empty_output(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import patch
    from worker.worker import render_video

    with patch("worker.worker.shutil.which", side_effect=["ffmpeg", None]), patch(
        "worker.worker.subprocess.run",
        return_value=SimpleNamespace(returncode=0, stderr=""),
    ):
        with pytest.raises(RuntimeError, match="output_exists=False, output_size=0 bytes"):
            render_video(
                {"id": "job-id", "topic": "topic", "script": "", "duration_seconds": 15},
                tmp_path,
            )


def test_render_video_maps_generated_voiceover(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import patch
    from worker.worker import render_video

    image = tmp_path / "input.png"
    image.write_bytes(b"image")
    voiceover = tmp_path / "voiceover.mp3"
    voiceover.write_bytes(b"audio")

    def fake_run(command, **_kwargs):
        if command[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stdout="audio\n", stderr="")
        Path(command[-1]).write_bytes(b"x" * 2048)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch("worker.worker.shutil.which", side_effect=["ffmpeg", "ffprobe"]), patch(
        "worker.worker._audio_duration", return_value=None
    ), patch(
        "worker.worker.subprocess.run", side_effect=fake_run
    ) as run:
        render_video(
            {"id": "job-id", "topic": "Cybersecurity", "script": "Use unique passwords"},
            tmp_path,
            image_path=image,
            audio_path=voiceover,
        )

    command = next(call.args[0] for call in run.call_args_list if "-map" in call.args[0])
    assert command[command.index("-map") + 1] == "0:v:0"
    audio_map = command.index("1:a:0")
    assert command[audio_map - 1] == "-map"
    assert "-c:a" in command
    assert "-an" not in command


def test_render_video_rejects_missing_narration_track(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import patch
    from worker.worker import render_video

    image = tmp_path / "input.png"
    image.write_bytes(b"image")
    voiceover = tmp_path / "voiceover.mp3"
    voiceover.write_bytes(b"audio")

    def fake_run(command, **_kwargs):
        if command[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        Path(command[-1]).write_bytes(b"x" * 2048)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch("worker.worker.shutil.which", side_effect=["ffmpeg", "ffprobe"]), patch(
        "worker.worker._audio_duration", return_value=None
    ), patch("worker.worker.subprocess.run", side_effect=fake_run):
        with pytest.raises(RuntimeError, match="without the required narration audio track"):
            render_video(
                {"id": "job-id", "topic": "Cybersecurity", "script": "Use unique passwords"},
                tmp_path,
                image_path=image,
                audio_path=voiceover,
            )


def test_storyboard_images_are_cybersecurity_illustrations(tmp_path):
    from PIL import Image
    from worker.worker import _create_storyboard_images

    images = _create_storyboard_images(
        {
            "id": "cyber-job",
            "topic": "साइबर सुरक्षा: फिशिंग लिंक और OTP",
            "script": "फिशिंग लिंक जाँचें और OTP साझा न करें।",
        },
        tmp_path,
    )

    assert len(images) == 4
    for path in images:
        with Image.open(path) as image:
            assert image.size == (720, 1280)
            assert image.format == "PNG"


def test_extract_job_accepts_empty_queue_and_valid_job():
    from worker.worker import _extract_job

    job_id = "cd8b1fc9-da18-4a8f-bb36-fa177d93af0e"
    assert _extract_job({"job": None}) is None
    job = _extract_job(
        {
            "job": {
                "id": job_id,
                "topic": "Hindi facts",
                "assets": {"image": "job-scene.png"},
            }
        }
    )
    assert job is not None
    assert job["id"] == job_id


def test_extract_job_rejects_unsafe_asset_path():
    from worker.worker import _extract_job

    with pytest.raises(ValueError, match="unsafe render asset"):
        _extract_job(
            {
                "job": {
                    "id": "cd8b1fc9-da18-4a8f-bb36-fa177d93af0e",
                    "topic": "Hindi facts",
                    "assets": {"image": "..\\outside.png"},
                }
            }
        )


def test_extract_job_accepts_platform_thumbnail_names():
    from worker.worker import _extract_job

    job = _extract_job({
        "job": {
            "id": "cd8b1fc9-da18-4a8f-bb36-fa177d93af0e",
            "topic": "Hindi facts",
            "assets": {},
            "thumbnail_names": {
                "youtube": "cd8b1fc9-youtube.jpg",
                "instagram": "cd8b1fc9-instagram.jpg",
            },
        }
    })
    assert job is not None
    assert set(job["thumbnail_names"]) == {"youtube", "instagram"}


def test_extract_job_rejects_unsafe_thumbnail_name():
    from worker.worker import _extract_job

    with pytest.raises(ValueError, match="unsafe thumbnail"):
        _extract_job({
            "job": {
                "id": "cd8b1fc9-da18-4a8f-bb36-fa177d93af0e",
                "topic": "Hindi facts",
                "assets": {},
                "thumbnail_names": {"youtube": "..\\outside.jpg"},
            }
        })


def test_render_thumbnails_creates_platform_sizes(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import patch
    from worker.worker import render_thumbnails

    job_id = "cd8b1fc9-da18-4a8f-bb36-fa177d93af0e"
    names = {
        "youtube": f"{job_id}-youtube.jpg",
        "instagram": f"{job_id}-instagram.jpg",
    }

    def fake_run(command, **_kwargs):
        Path(command[-1]).write_bytes(b"\xff\xd8\xff" + b"x" * 2048)
        return SimpleNamespace(returncode=0, stderr="")

    with patch("worker.worker.shutil.which", return_value="ffmpeg"), patch(
        "worker.worker.subprocess.run", side_effect=fake_run
    ) as run:
        result = render_thumbnails(
            {"id": job_id, "topic": "Hindi topic", "title": "A polished title", "thumbnail_names": names},
            tmp_path / "render.mp4",
            tmp_path,
        )

    assert set(result) == {"youtube", "instagram"}
    assert len(run.call_args_list) == 2
    assert "scale=1280:720" in " ".join(run.call_args_list[0].args[0])
    assert "scale=1080:1920" in " ".join(run.call_args_list[1].args[0])
