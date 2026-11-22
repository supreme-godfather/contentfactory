"""Delivery profile for rendered media.

The render worker measures the file it produced (resolution, frame rate, audio
presence, integrated loudness, true peak). This module owns the delivery
profile and turns those measurements into blockers and warnings. A blocker
stops publication; a warning is surfaced to the operator but does not stop it.
"""
from typing import Any

DELIVERY_PROFILE: dict[str, float] = {
    "min_width": 720,
    "min_height": 720,
    "max_width": 4096,
    "max_height": 4096,
    "min_fps": 20.0,
    "max_fps": 60.0,
    "min_duration_seconds": 3.0,
    "max_duration_seconds": 900.0,
    "target_lufs": -14.0,
    "max_lufs_deviation": 2.5,
    "max_true_peak_dbfs": -1.0,
    "min_bytes": 50000,
}

BLOCKER_LABELS: dict[str, str] = {
    "media_not_measured": "the rendered video was never measured",
    "media_probe_failed": "the rendered video could not be inspected",
    "no_video_stream": "the render has no video stream",
    "no_audio_stream": "the render has no audio track",
    "resolution_too_small": "the render resolution is below the delivery minimum",
    "resolution_too_large": "the render resolution is above the delivery maximum",
    "unsupported_pixel_format": "the render uses an unsupported pixel format",
    "duration_too_short": "the render is too short to publish",
    "file_too_small": "the render file is suspiciously small",
}

WARNING_LABELS: dict[str, str] = {
    "loudness_outside_target": "the integrated loudness is outside the delivery target",
    "true_peak_too_high": "the true peak may clip on loud devices",
    "fps_outside_delivery_profile": "the frame rate is outside the delivery profile",
    "duration_above_profile": "the render is longer than the delivery profile allows",
}


def _number(report: dict[str, Any], key: str) -> float:
    value = report.get(key)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def evaluate_report(report: dict[str, Any] | None) -> tuple[list[str], list[str]]:
    """Return (blockers, warnings) for a media measurement report."""
    if not report or not isinstance(report, dict):
        return ["media_not_measured"], []
    if not report.get("measured"):
        return ["media_probe_failed"], []
    if not report.get("probe_ok"):
        return ["media_probe_failed"], []

    blockers: list[str] = []
    warnings: list[str] = []

    if "width" not in report or "height" not in report:
        blockers.append("no_video_stream")
    else:
        width = _number(report, "width")
        height = _number(report, "height")
        if width < DELIVERY_PROFILE["min_width"] or height < DELIVERY_PROFILE["min_height"]:
            blockers.append("resolution_too_small")
        if width > DELIVERY_PROFILE["max_width"] or height > DELIVERY_PROFILE["max_height"]:
            blockers.append("resolution_too_large")

    fps = _number(report, "fps")
    if fps and not DELIVERY_PROFILE["min_fps"] <= fps <= DELIVERY_PROFILE["max_fps"]:
        warnings.append("fps_outside_delivery_profile")

    if not report.get("has_audio"):
        blockers.append("no_audio_stream")

    duration = _number(report, "duration_seconds")
    if duration and duration < DELIVERY_PROFILE["min_duration_seconds"]:
        blockers.append("duration_too_short")
    if duration > DELIVERY_PROFILE["max_duration_seconds"]:
        warnings.append("duration_above_profile")

    size = _number(report, "bytes")
    if size and size < DELIVERY_PROFILE["min_bytes"]:
        blockers.append("file_too_small")

    if "integrated_lufs" in report:
        lufs = _number(report, "integrated_lufs")
        target = DELIVERY_PROFILE["target_lufs"]
        deviation = DELIVERY_PROFILE["max_lufs_deviation"]
        if abs(lufs - target) > deviation:
            warnings.append("loudness_outside_target")
    if "true_peak_dbfs" in report:
        if _number(report, "true_peak_dbfs") > DELIVERY_PROFILE["max_true_peak_dbfs"]:
            warnings.append("true_peak_too_high")

    return blockers, warnings


def qc_findings(metadata: dict[str, Any] | None) -> tuple[list[str], list[str]]:
    """Evaluate the media QC report stored on a job's metadata."""
    if not isinstance(metadata, dict):
        return ["media_not_measured"], []
    report = metadata.get("media_qc")
    if not isinstance(report, dict):
        return ["media_not_measured"], []
    return evaluate_report(report)


def describe(blockers: list[str], warnings: list[str]) -> str:
    parts = [BLOCKER_LABELS.get(item, item.replace("_", " ")) for item in blockers]
    parts += [WARNING_LABELS.get(item, item.replace("_", " ")) for item in warnings]
    return "; ".join(parts)
