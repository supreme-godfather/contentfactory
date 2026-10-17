import json

from app.quality import validate_content


def test_quality_blocks_missing_render():
    report = validate_content(
        topic="AI",
        script="यह एक पर्याप्त रूप से लंबी हिंदी स्क्रिप्ट है जो गुणवत्ता जांच के लिए आवश्यक न्यूनतम लंबाई से अधिक है।",
        metadata_json=json.dumps({"title": "AI", "description": "Description"}),
        render_path=None,
    )

    assert report.passed is False
    assert "missing_render" in report.blockers


def test_quality_accepts_complete_package():
    report = validate_content(
        topic="AI",
        script="यह एक पर्याप्त रूप से लंबी हिंदी स्क्रिप्ट है जो गुणवत्ता जांच के लिए आवश्यक न्यूनतम लंबाई से अधिक है।",
        metadata_json=json.dumps({"title": "AI", "description": "Description"}),
        render_path="media/video.mp4",
    )

    assert report.passed is True
