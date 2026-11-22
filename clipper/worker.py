import argparse
import html
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import statistics
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

import httpx

WORKER_ID = "railway-clipper"
VIDEO_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov"}
EXCITEMENT_MARKERS = (
    "?", "!", "how", "why", "secret", "truth", "never", "best", "worst",
    "क्या", "क्यों", "कैसे", "सच", "राज़", "सबसे", "देखिए", "रुकिए",
)


def validate_source_url(value: str, *, resolve_dns: bool = True) -> str:
    clean = value.strip()
    if not clean or len(clean) > 2048 or any(ord(char) < 32 for char in clean):
        raise ValueError("Source URL is invalid")
    parsed = urlsplit(clean)
    host = (parsed.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.port not in {None, 443}
        or host in {"localhost", "localhost.localdomain"}
        or host.endswith((".local", ".internal", ".localhost"))
        or "." not in host
    ):
        raise ValueError("Source must be a public HTTPS URL")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("Source URL cannot use an IP literal")
    if resolve_dns:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        }
        if not addresses:
            raise ValueError("Source hostname did not resolve")
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise ValueError("Source hostname resolves to a non-public address")
    return clean


def _run(command: list[str], *, timeout: int, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        env={
            **os.environ,
            "HOME": str(cwd),
            "XDG_CONFIG_HOME": str(cwd / ".config"),
            "XDG_CACHE_HOME": str(cwd / ".cache"),
        },
    )


def _safe_error_text(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http_{exc.response.status_code}_{exc.request.url.host or 'unknown'}"
    message = " ".join(str(exc).split())
    if isinstance(exc, (RuntimeError, ValueError)) and message:
        return message[:400]
    return type(exc).__name__


def _probe(source: Path) -> dict[str, object]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("FFprobe is required")
    result = _run(
        [
            ffprobe,
            "-v", "error",
            "-show_entries", "format=duration,size:stream=codec_type,width,height,r_frame_rate",
            "-of", "json",
            str(source),
        ],
        timeout=60,
        cwd=source.parent,
    )
    if result.returncode != 0:
        raise RuntimeError("source_probe_failed")
    payload = json.loads(result.stdout)
    duration = float((payload.get("format") or {}).get("duration") or 0)
    if duration < 45:
        raise RuntimeError("insufficient_distinct_material")
    streams = payload.get("streams") or []
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if not video:
        raise RuntimeError("source_has_no_video")
    return {
        "duration_ms": round(duration * 1000),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "has_audio": any(stream.get("codec_type") == "audio" for stream in streams),
    }


def measure_candidate(output: Path) -> dict[str, object]:
    """Measure a rendered clip against the delivery profile."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("FFprobe is required")
    result = _run(
        [
            ffprobe,
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(output),
        ],
        timeout=60,
        cwd=output.parent,
    )
    if result.returncode != 0:
        return {"ok": False, "blockers": ["probe_failed"]}
    payload = json.loads(result.stdout)
    streams = payload.get("streams") or []
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    blockers: list[str] = []
    width = int(video.get("width") or 0) if video else 0
    height = int(video.get("height") or 0) if video else 0
    if not video:
        blockers.append("no_video_stream")
    elif width < 720 or height < 720:
        blockers.append("resolution_too_small")
    if not audio:
        blockers.append("no_audio_stream")
    duration = float((payload.get("format") or {}).get("duration") or 0)
    if duration < 10:
        blockers.append("duration_too_short")
    size = output.stat().st_size
    if size < 50000:
        blockers.append("file_too_small")
    return {
        "ok": not blockers,
        "blockers": blockers,
        "width": width,
        "height": height,
        "duration_seconds": round(duration, 2),
        "bytes": size,
    }


def _parse_vtt_timestamp(value: str) -> int:
    parts = value.replace(",", ".").split(":")
    if len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        raise ValueError("Invalid VTT timestamp")
    return round((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000)


def parse_vtt(path: Path) -> list[dict[str, object]]:
    cues: list[dict[str, object]] = []
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if "-->" not in line:
            index += 1
            continue
        left, right = [part.strip().split(" ", 1)[0] for part in line.split("-->", 1)]
        start_ms = _parse_vtt_timestamp(left)
        end_ms = _parse_vtt_timestamp(right)
        index += 1
        text_lines = []
        while index < len(lines) and lines[index].strip():
            cleaned = re.sub(r"<[^>]+>", "", lines[index]).strip()
            if cleaned:
                text_lines.append(cleaned)
            index += 1
        text = " ".join(text_lines)
        text = re.sub(r"\s+", " ", html.unescape(text)).strip()
        if text and 0 <= start_ms < end_ms:
            if cues and cues[-1]["text"] == text and start_ms <= int(cues[-1]["end_ms"]) + 250:
                cues[-1]["end_ms"] = end_ms
            else:
                cues.append({"start_ms": start_ms, "end_ms": end_ms, "text": text})
        index += 1
    return cues


def transcription_segments_to_cues(segments: list[dict[str, object]]) -> list[dict[str, object]]:
    cues = []
    current = []
    for segment in segments:
        try:
            start_ms = round(float(segment["start"]) * 1000)
            end_ms = round(float(segment["end"]) * 1000)
        except (KeyError, TypeError, ValueError):
            continue
        text = " ".join(str(segment.get("text") or "").split())
        if not text or end_ms <= start_ms:
            continue
        candidate_text = " ".join([str(item["text"]) for item in current] + [text])
        gap = start_ms - int(current[-1]["end_ms"]) if current else 0
        if current and (len(current) >= 4 or len(candidate_text) > 38 or gap > 800):
            cues.append({
                "start_ms": int(current[0]["start_ms"]),
                "end_ms": int(current[-1]["end_ms"]),
                "text": " ".join(str(item["text"]) for item in current),
                "words": list(current),
            })
            current = []
        current.append({"start_ms": start_ms, "end_ms": end_ms, "text": text})
    if current:
        cues.append({
            "start_ms": int(current[0]["start_ms"]),
            "end_ms": int(current[-1]["end_ms"]),
            "text": " ".join(str(item["text"]) for item in current),
            "words": list(current),
        })
    return cues


def detect_audio_energy_events(source: Path, duration_ms: int) -> list[dict[str, object]]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return []
    result = _run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "info", "-i", str(source), "-vn",
            "-af",
            "aresample=16000,asetnsamples=n=16000:p=1,"
            "astats=metadata=1:reset=1,"
            "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
            "-f", "null", "-",
        ],
        timeout=min(900, max(120, duration_ms // 1000 + 120)),
        cwd=source.parent,
    )
    output = f"{result.stdout}\n{result.stderr}"
    values = []
    current_time = None
    for line in output.splitlines():
        time_match = re.search(r"pts_time:([0-9.]+)", line)
        if time_match:
            current_time = float(time_match.group(1))
        level_match = re.search(r"lavfi\.astats\.Overall\.RMS_level=(-?[0-9.]+)", line)
        if level_match and current_time is not None:
            values.append((round(current_time * 1000), float(level_match.group(1))))
    if len(values) < 5:
        return []
    levels = [level for _, level in values]
    median = statistics.median(levels)
    deviation = statistics.median(abs(level - median) for level in levels) or 1.0
    threshold = max(-35.0, median + 2.5 * deviation)
    raw = [
        {
            "time_ms": time_ms,
            "kind": "audio_peak",
            "strength": min(1000, max(1, round((level - threshold + 1) * 120))),
        }
        for time_ms, level in values
        if level >= threshold
    ]
    selected = []
    for event in sorted(raw, key=lambda item: (-int(item["strength"]), int(item["time_ms"]))):
        if all(abs(int(event["time_ms"]) - int(other["time_ms"])) >= 5000 for other in selected):
            selected.append(event)
    return sorted(selected[:30], key=lambda item: int(item["time_ms"]))


def select_candidates(
    duration_ms: int,
    cues: list[dict[str, object]],
    signal_events: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    if duration_ms < 45000:
        raise ValueError("insufficient_distinct_material")
    third = duration_ms // 3
    candidates = []
    for section in range(3):
        section_start = section * third
        section_end = duration_ms if section == 2 else (section + 1) * third
        window_duration = min(30000, section_end - section_start)
        window_duration = max(15000, window_duration)
        section_cues = [
            cue for cue in cues
            if section_start <= int(cue["start_ms"]) < section_end
        ]
        section_events = [
            event for event in (signal_events or [])
            if section_start <= int(event.get("time_ms") or -1) < section_end
        ]
        anchor = None
        if section_cues:
            anchor = max(
                section_cues,
                key=lambda cue: (
                    sum(marker.casefold() in str(cue["text"]).casefold() for marker in EXCITEMENT_MARKERS),
                    len(str(cue["text"])),
                    -int(cue["start_ms"]),
                ),
            )
        strongest_event = max(
            section_events,
            key=lambda event: (int(event.get("strength") or 0), -int(event.get("time_ms") or 0)),
            default=None,
        )
        if strongest_event is not None:
            desired_start = int(strongest_event["time_ms"]) - 6000
        elif anchor is not None:
            desired_start = int(anchor["start_ms"]) - 3000
        else:
            desired_start = (section_start + section_end - window_duration) // 2
        start_ms = max(section_start, min(desired_start, section_end - window_duration))
        end_ms = min(section_end, start_ms + window_duration)
        included = [
            cue for cue in section_cues
            if int(cue["end_ms"]) > start_ms and int(cue["start_ms"]) < end_ms
        ]
        transcript = " ".join(str(cue["text"]) for cue in included)
        excitement = sum(
            marker.casefold() in transcript.casefold()
            for marker in EXCITEMENT_MARKERS
        )
        signal_strength = int((strongest_event or {}).get("strength") or 0)
        score = min(1000, 500 + excitement * 45 + min(200, len(included) * 8) + signal_strength // 5)
        title_words = transcript.split()[:9]
        title = " ".join(title_words) or f"Highlight {section + 1}"
        candidates.append({
            "start_ms": int(start_ms),
            "end_ms": int(end_ms),
            "score": int(score),
            "title": title[:200],
            "reason": (
                "subtitle_and_audio_peak"
                if included and strongest_event
                else "subtitle_hook"
                if included
                else "audio_peak"
                if strongest_event
                else "balanced_source_section"
            ),
            "transcript_excerpt": transcript[:3000],
            "cues": included,
        })
    ranked = sorted(candidates, key=lambda item: (-int(item["score"]), int(item["start_ms"])))
    for rank, candidate in enumerate(ranked, start=1):
        candidate["rank"] = rank
    return ranked


def _ass_time(milliseconds: int) -> str:
    centiseconds = max(0, round(milliseconds / 10))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    seconds, fraction = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{fraction:02d}"


def _ass_escape(value: str) -> str:
    return value.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")


def write_candidate_ass(candidate: dict[str, object], path: Path) -> None:
    start_ms = int(candidate["start_ms"])
    end_ms = int(candidate["end_ms"])
    events = []
    for cue in candidate.get("cues") or []:
        local_start = max(0, int(cue["start_ms"]) - start_ms)
        local_end = min(end_ms - start_ms, int(cue["end_ms"]) - start_ms)
        if local_end <= local_start:
            continue
        words = cue.get("words") if isinstance(cue, dict) else None
        if isinstance(words, list) and words:
            parts = []
            for word in words:
                word_text = _ass_escape(str(word.get("text") or ""))
                word_duration = max(
                    1,
                    round((float(word.get("end_ms") or 0) - float(word.get("start_ms") or 0)) / 10),
                )
                if word_text:
                    parts.append(f"{{\\kf{word_duration}}}{word_text}")
            event_text = " ".join(parts) or _ass_escape(str(cue["text"]))
        else:
            event_text = _ass_escape(str(cue["text"]))
        events.append(
            f"Dialogue: 0,{_ass_time(local_start)},{_ass_time(local_end)},Default,,0,0,0,,"
            f"{{\\fad(60,60)}}{event_text}"
        )
    if not events:
        events.append(
            f"Dialogue: 0,0:00:00.00,0:00:03.00,Title,,0,0,0,,{_ass_escape(str(candidate['title']))}"
        )
    path.write_text(
        "\n".join([
            "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "WrapStyle: 2", "",
            "[V4+ Styles]",
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
            "Style: Default,Noto Sans Devanagari,60,&H00FFFFFF,&H0000D7FF,&H00101010,&H78000000,-1,0,0,0,100,100,0,0,1,5,1,2,70,180,410,1",
            "Style: Title,Noto Sans Devanagari,68,&H00FFFFFF,&H0000D7FF,&H00101010,&H78000000,-1,0,0,0,100,100,0,0,1,5,1,8,70,180,250,1",
            "", "[Events]", "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text", *events, "",
        ]),
        encoding="utf-8",
    )


def render_candidate(source: Path, candidate: dict[str, object], output: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required")
    ass_path = output.with_suffix(".ass")
    write_candidate_ass(candidate, ass_path)
    start = int(candidate["start_ms"]) / 1000
    duration = (int(candidate["end_ms"]) - int(candidate["start_ms"])) / 1000
    ass_filter = str(ass_path.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
    filtergraph = (
        "[0:v]split=2[background][foreground];"
        "[background]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,boxblur=24:2[background];"
        "[foreground]scale=1080:1920:force_original_aspect_ratio=decrease[foreground];"
        "[background][foreground]overlay=(W-w)/2:(H-h)/2,"
        f"ass=filename='{ass_filter}':fontsdir='/usr/share/fonts/truetype/noto'[v]"
    )
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostats", "-y",
        "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}",
        "-filter_complex", filtergraph,
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "160k", "-af", "loudnorm=I=-16:LRA=7:TP=-1.5",
        "-movflags", "+faststart", str(output),
    ]
    result = _run(command, timeout=600, cwd=output.parent)
    if result.returncode != 0 or not output.is_file() or output.stat().st_size < 1024:
        raise RuntimeError("clip_render_failed")
    if output.stat().st_size > 49 * 1024 * 1024:
        raise RuntimeError("clip_render_exceeds_preview_limit")


def render_thumbnails(video: Path, output_dir: Path, rank: int) -> tuple[Path, Path]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required")
    youtube = output_dir / f"candidate-{rank}-youtube.jpg"
    instagram = output_dir / f"candidate-{rank}-instagram.jpg"
    for destination, width, height in (
        (youtube, 1280, 720),
        (instagram, 1080, 1920),
    ):
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", "1", "-i", str(video),
            "-frames:v", "1", "-vf",
            f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}",
            "-q:v", "2", str(destination),
        ]
        result = _run(command, timeout=90, cwd=output_dir)
        if result.returncode != 0 or not destination.is_file() or destination.stat().st_size < 1024:
            raise RuntimeError("clip_thumbnail_failed")
    return youtube, instagram


def extract_analysis_audio(source: Path, destination: Path, duration_ms: int) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required")
    duration = min(3600, max(1, duration_ms // 1000))
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
        "-t", str(duration), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k",
        str(destination),
    ]
    result = _run(command, timeout=min(900, duration + 300), cwd=destination.parent)
    if result.returncode != 0 or not destination.is_file() or destination.stat().st_size < 256:
        raise RuntimeError("analysis_audio_extraction_failed")
    if destination.stat().st_size > 25 * 1024 * 1024:
        raise RuntimeError("analysis_audio_exceeds_transcription_limit")


def download_direct_media(url: str, work_dir: Path, maximum_bytes: int) -> Path:
    current = validate_source_url(url)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
        ),
        "Accept": "video/*,application/octet-stream;q=0.9,*/*;q=0.1",
    }
    destination = work_dir / "source.mp4"
    with httpx.Client(timeout=httpx.Timeout(600, connect=20), follow_redirects=False) as client:
        for _ in range(6):
            with client.stream("GET", current, headers=headers) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise RuntimeError("direct_download_redirect_missing_location")
                    current = validate_source_url(str(response.url.join(location)))
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if content_type and not (
                    content_type.startswith("video/")
                    or content_type == "application/octet-stream"
                ):
                    raise RuntimeError("direct_download_returned_non_video_content")
                total = 0
                with destination.open("wb") as output:
                    for chunk in response.iter_bytes(1024 * 1024):
                        total += len(chunk)
                        if total > maximum_bytes:
                            raise RuntimeError("source_exceeds_size_limit")
                        output.write(chunk)
                if total < 1024:
                    raise RuntimeError("direct_download_returned_empty_video")
                return destination
        raise RuntimeError("direct_download_exceeded_redirect_limit")


def download_source(job: dict[str, object], work_dir: Path) -> tuple[Path, dict[str, object], list[dict[str, object]]]:
    url = validate_source_url(str(job["source_url"]))
    maximum_bytes = int((job.get("limits") or {}).get("max_source_bytes") or 1024 * 1024 * 1024)
    direct_suffix = Path(urlsplit(url).path).suffix.lower()
    if job.get("source_kind") == "vod" and direct_suffix in VIDEO_SUFFIXES:
        try:
            source = download_direct_media(url, work_dir, maximum_bytes)
            return source, {"extractor": "direct", "title": source.stem, "is_live": False}, []
        except (httpx.HTTPError, RuntimeError):
            (work_dir / "source.mp4").unlink(missing_ok=True)
    yt_dlp = shutil.which("yt-dlp")
    if not yt_dlp:
        raise RuntimeError("yt_dlp_is_required")
    output_template = str(work_dir / "source.%(ext)s")
    command = [
        yt_dlp, "--ignore-config", "--no-playlist", "--no-cache-dir", "--no-progress", "--no-warnings",
        "--impersonate", "chrome",
        "--restrict-filenames", "--write-info-json", "--write-subs", "--write-auto-subs",
        "--sub-langs", "en.*,hi.*", "--sub-format", "vtt",
        "--format", "bv*[height<=1080]+ba/b[height<=1080]/best", "--merge-output-format", "mp4",
        "--max-filesize", str(maximum_bytes), "--output", output_template,
    ]
    timeout = 2400
    if job.get("source_kind") == "live":
        capture = int(job.get("capture_seconds") or 900)
        command.extend(["--downloader", "ffmpeg", "--downloader-args", f"ffmpeg_i:-t {capture}"])
        timeout = capture + 600
    command.append(url)
    result = _run(command, timeout=timeout, cwd=work_dir)
    if result.returncode != 0:
        detail = " ".join((result.stderr or result.stdout).split())[-300:]
        detail = detail.replace(url, "[source]")
        raise RuntimeError(f"source_download_failed: {detail}" if detail else "source_download_failed")
    sources = [path for path in work_dir.glob("source.*") if path.suffix.lower() in VIDEO_SUFFIXES]
    if len(sources) != 1:
        raise RuntimeError("source_download_produced_invalid_files")
    source = sources[0]
    if source.stat().st_size > maximum_bytes:
        raise RuntimeError("source_exceeds_size_limit")
    info_files = list(work_dir.glob("source*.info.json"))
    info = json.loads(info_files[0].read_text(encoding="utf-8")) if info_files else {}
    cues = []
    for subtitle in sorted(work_dir.glob("source*.vtt")):
        parsed = parse_vtt(subtitle)
        if len(parsed) > len(cues):
            cues = parsed
    return source, info, cues


def _runtime_check() -> None:
    for tool in ("ffmpeg", "ffprobe", "yt-dlp"):
        if not shutil.which(tool):
            raise RuntimeError(f"{tool} is required")
    ffmpeg = shutil.which("ffmpeg")
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-h", "filter=ass"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError("FFmpeg ASS support is required")


def process_job(client: httpx.Client, job: dict[str, object], work_root: Path) -> None:
    job_id = str(job["id"])
    lease_token = str(job["lease_token"])
    headers = {"X-Clip-Lease-Token": lease_token}
    with tempfile.TemporaryDirectory(prefix=f"clip-{job_id}-", dir=work_root) as temp:
        work_dir = Path(temp)
        try:
            source, info, cues = download_source(job, work_dir)
            probe = _probe(source)
            energy_events = detect_audio_energy_events(source, int(probe["duration_ms"])) if probe.get("has_audio") else []
            client.post(
                f"/api/clip-worker/jobs/{job_id}/lease/renew",
                params={"worker_id": WORKER_ID},
                headers=headers,
                json={"stage": "analyse"},
            ).raise_for_status()
            if not cues and probe.get("has_audio"):
                analysis_audio = work_dir / "analysis-audio.mp3"
                extract_analysis_audio(source, analysis_audio, int(probe["duration_ms"]))
                with analysis_audio.open("rb") as audio:
                    transcription = client.post(
                        f"/api/clip-worker/jobs/{job_id}/transcribe",
                        params={"worker_id": WORKER_ID},
                        headers=headers,
                        files={"file": (analysis_audio.name, audio, "audio/mpeg")},
                    )
                if transcription.status_code == 200:
                    payload = transcription.json()
                    segments = payload.get("segments")
                    if isinstance(segments, list):
                        cues = transcription_segments_to_cues(segments)
                elif transcription.status_code not in {409, 502}:
                    transcription.raise_for_status()
            candidates = select_candidates(int(probe["duration_ms"]), cues, energy_events)
            client.post(
                f"/api/clip-worker/jobs/{job_id}/lease/renew",
                params={"worker_id": WORKER_ID},
                headers=headers,
                json={"stage": "render"},
            ).raise_for_status()
            registered = 0
            for candidate in candidates:
                rank = int(candidate["rank"])
                output = work_dir / f"candidate-{rank}.mp4"
                render_candidate(source, candidate, output)
                measurement = measure_candidate(output)
                if not measurement.get("ok"):
                    print(
                        f"Skipping clip candidate {rank}: "
                        f"{measurement.get('blockers')}",
                        flush=True,
                    )
                    continue
                youtube_thumbnail, instagram_thumbnail = render_thumbnails(output, work_dir, rank)
                with (
                    output.open("rb") as media,
                    youtube_thumbnail.open("rb") as youtube_cover,
                    instagram_thumbnail.open("rb") as instagram_cover,
                ):
                    response = client.post(
                        f"/api/clip-worker/jobs/{job_id}/candidates/{rank}",
                        params={
                            "worker_id": WORKER_ID,
                            "start_ms": int(candidate["start_ms"]),
                            "end_ms": int(candidate["end_ms"]),
                            "score": int(candidate["score"]),
                            "title": str(candidate["title"]),
                            "reason": str(candidate["reason"]),
                            "transcript_excerpt": str(candidate["transcript_excerpt"]),
                        },
                        headers=headers,
                        files={
                            "file": (output.name, media, "video/mp4"),
                            "youtube_thumbnail": (youtube_thumbnail.name, youtube_cover, "image/jpeg"),
                            "instagram_thumbnail": (instagram_thumbnail.name, instagram_cover, "image/jpeg"),
                        },
                    )
                response.raise_for_status()
                registered += 1
            if registered == 0:
                raise RuntimeError("no_renderable_candidates")
            client.post(
                f"/api/clip-worker/jobs/{job_id}/complete",
                params={"worker_id": WORKER_ID},
                headers=headers,
                json={
                    "duration_ms": int(probe["duration_ms"]),
                    "extractor": str(info.get("extractor_key") or info.get("extractor") or "generic")[:80],
                    "title": str(info.get("title") or "")[:300],
                    "actual_kind": "live" if info.get("is_live") else str(job.get("source_kind") or "vod"),
                    "caption_source": "timed_transcript" if cues else "none",
                    "audio_signal_events": len(energy_events),
                    "source_id": str(info.get("id") or "")[:160],
                    "creator": str(info.get("channel") or info.get("uploader") or "")[:160],
                },
            ).raise_for_status()
        except Exception as exc:
            safe_error = _safe_error_text(exc)
            try:
                client.post(
                    f"/api/clip-worker/jobs/{job_id}/fail",
                    params={"worker_id": WORKER_ID},
                    headers=headers,
                    json={"error_code": safe_error[:200]},
                ).raise_for_status()
            except httpx.HTTPError:
                pass
            raise RuntimeError(safe_error) from exc


def run(factory_url: str, worker_token: str, poll_seconds: int, work_root: Path) -> None:
    work_root.mkdir(parents=True, exist_ok=True)
    headers = {"X-Worker-Token": worker_token}
    timeout = httpx.Timeout(300, connect=20)
    with httpx.Client(base_url=factory_url.rstrip("/"), headers=headers, timeout=timeout) as client:
        while True:
            try:
                response = client.post(
                    "/api/clip-worker/claim",
                    params={"worker_id": WORKER_ID},
                )
                response.raise_for_status()
                job = response.json().get("job")
                if job is None:
                    time.sleep(poll_seconds)
                    continue
                print(f"Claimed clip job {job['id']}", flush=True)
                process_job(client, job, work_root)
                print(f"Completed clip job {job['id']}", flush=True)
            except Exception as exc:
                print(f"Clip worker error: {_safe_error_text(exc)}; retrying.", flush=True)
            time.sleep(poll_seconds)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Content Factory clipping worker")
    parser.add_argument("--url", default=os.environ.get("FACTORY_URL", ""))
    parser.add_argument("--token", default=os.environ.get("WORKER_TOKEN", ""))
    parser.add_argument("--poll-seconds", type=int, default=int(os.environ.get("CLIP_POLL_SECONDS", "30")))
    parser.add_argument("--work-dir", default=os.environ.get("CLIP_WORK_DIR", "/tmp/factory-clips"))
    args = parser.parse_args()
    if not args.url.startswith("https://"):
        raise SystemExit("FACTORY_URL must use HTTPS")
    if not args.token or any(ord(char) < 33 or ord(char) > 126 for char in args.token):
        raise SystemExit("WORKER_TOKEN is required and must contain printable ASCII")
    _runtime_check()
    print("Clip runtime ready: pinned yt-dlp, FFmpeg, ASS captions, and bounded work directory.", flush=True)
    run(args.url, args.token, max(5, args.poll_seconds), Path(args.work_dir))
