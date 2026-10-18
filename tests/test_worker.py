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
