import json

from app.media_qc import evaluate_report, qc_findings


def _report(**overrides):
    report = {
        "measured": True,
        "probe_ok": True,
        "width": 1080,
        "height": 1920,
        "fps": 30.0,
        "duration_seconds": 48.0,
        "has_audio": True,
        "bytes": 4_000_000,
        "integrated_lufs": -14.0,
        "true_peak_dbfs": -1.5,
    }
    report.update(overrides)
    return report


def test_missing_report_is_a_blocker():
    blockers, warnings = evaluate_report(None)

    assert blockers == ["media_not_measured"]
    assert warnings == []


def test_probe_failure_is_a_blocker():
    blockers, _warnings = evaluate_report({"measured": True, "probe_ok": False})

    assert blockers == ["media_probe_failed"]


def test_good_report_passes_without_findings():
    blockers, warnings = evaluate_report(_report())

    assert blockers == []
    assert warnings == []


def test_missing_audio_blocks_publication():
    blockers, _warnings = evaluate_report(_report(has_audio=False))

    assert "no_audio_stream" in blockers


def test_small_resolution_blocks_publication():
    blockers, _warnings = evaluate_report(_report(width=480, height=854))

    assert "resolution_too_small" in blockers


def test_loudness_outside_target_is_only_a_warning():
    blockers, warnings = evaluate_report(_report(integrated_lufs=-26.0))

    assert blockers == []
    assert "loudness_outside_target" in warnings


def test_true_peak_above_limit_warns():
    blockers, warnings = evaluate_report(_report(true_peak_dbfs=-0.2))

    assert blockers == []
    assert "true_peak_too_high" in warnings


def test_qc_findings_reads_job_metadata():
    metadata = {"media_qc": _report(has_audio=False)}

    blockers, _warnings = qc_findings(metadata)

    assert "no_audio_stream" in blockers


def test_qc_findings_without_report():
    blockers, _warnings = qc_findings({})

    assert blockers == ["media_not_measured"]


def test_worker_measurement_parses_probe_output(tmp_path, monkeypatch):
    from worker import media_qc

    media = tmp_path / "render.mp4"
    media.write_bytes(b"0" * 2048)

    probe_payload = json.dumps({
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1080,
                "height": 1920,
                "avg_frame_rate": "30/1",
                "pix_fmt": "yuv420p",
            },
            {"codec_type": "audio", "codec_name": "aac", "sample_rate": "44100", "channels": 2},
        ],
        "format": {"duration": "47.500000"},
    })

    class Result:
        def __init__(self, stdout: str, stderr: str = "") -> None:
            self.returncode = 0
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command, timeout=240):
        if "ffprobe" in command[0]:
            return Result(probe_payload)
        return Result("", "Summary\n  I: -13.8 LUFS\n  Peak: -1.4 dBFS\n")

    monkeypatch.setattr(media_qc, "_run", fake_run)

    report = media_qc.measure_media(media)

    assert report["measured"] is True
    assert report["probe_ok"] is True
    assert report["width"] == 1080
    assert report["height"] == 1920
    assert report["fps"] == 30.0
    assert report["duration_seconds"] == 47.5
    assert report["has_audio"] is True
    assert report["integrated_lufs"] == -13.8
    assert report["true_peak_dbfs"] == -1.4
