from pathlib import Path


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
