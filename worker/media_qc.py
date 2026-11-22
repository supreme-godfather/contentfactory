"""Media measurement for a rendered video.

The API container has no FFmpeg, so the render worker measures the file it
produced and posts the raw measurements to the API. The API owns the delivery
profile and decides whether the media may be published.
"""
import json
import re
import subprocess
from pathlib import Path


def _run(command: list[str], timeout: int = 240) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _fraction(value: str) -> float:
    if "/" in value:
        numerator, _, denominator = value.partition("/")
        try:
            divisor = float(denominator)
            return round(float(numerator) / divisor, 3) if divisor else 0.0
        except (TypeError, ValueError, ZeroDivisionError):
            return 0.0
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return 0.0


def measure_media(path: Path) -> dict[str, object]:
    """Measure a rendered file and return raw measurements."""
    report: dict[str, object] = {"measured": True, "probe_ok": False}
    try:
        report["bytes"] = path.stat().st_size
    except OSError as exc:
        report["measured"] = False
        report["error"] = type(exc).__name__
        return report

    probe = _run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    if probe.returncode != 0:
        report["error"] = (probe.stderr or "")[:200]
        return report
    try:
        payload = json.loads(probe.stdout or "{}")
    except json.JSONDecodeError:
        report["error"] = "probe_output_invalid"
        return report

    report["probe_ok"] = True
    streams = payload.get("streams") or []
    format_info = payload.get("format") or {}
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if video:
        fps = _fraction(str(video.get("avg_frame_rate") or "0")) or _fraction(
            str(video.get("r_frame_rate") or "0")
        )
        report["width"] = int(video.get("width") or 0)
        report["height"] = int(video.get("height") or 0)
        report["fps"] = fps
        report["video_codec"] = str(video.get("codec_name") or "")
        report["pix_fmt"] = str(video.get("pix_fmt") or "")
    report["has_audio"] = bool(audio)
    if audio:
        report["audio_codec"] = str(audio.get("codec_name") or "")
        report["sample_rate"] = int(audio.get("sample_rate") or 0)
        report["channels"] = int(audio.get("channels") or 0)
    try:
        report["duration_seconds"] = round(float(format_info.get("duration") or 0), 3)
    except (TypeError, ValueError):
        report["duration_seconds"] = 0.0

    loudness = _run([
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
        "-af", "ebur128=peak=true", "-f", "null", "-",
    ])
    text = f"{loudness.stdout}\n{loudness.stderr}"
    integrated = re.findall(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", text)
    peak = re.findall(r"Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS", text)
    if integrated:
        report["integrated_lufs"] = float(integrated[-1])
    if peak:
        report["true_peak_dbfs"] = float(peak[-1])
    return report
