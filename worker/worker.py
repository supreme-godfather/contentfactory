import argparse
import json
import os
from pathlib import Path
import re
import socket
import shutil
import subprocess
import time
from uuid import UUID

import httpx


def _configured_worker_id(value: str | None = None) -> str:
    worker_id = (value or os.environ.get("WORKER_ID", "")).strip() or socket.gethostname()
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", worker_id):
        raise ValueError("WORKER_ID must contain only letters, numbers, '.', '_', ':' or '-'.")
    return worker_id


def _escape_drawtext(value: str) -> str:
    return value.replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'").replace("%", r"\%").replace("\n", " ")


def _escape_font_path(value: str) -> str:
    return value.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def _scene_text(job: dict[str, str]) -> list[str]:
    paragraphs = [
        " ".join(part.split())
        for part in (job.get("script") or "").split("\n")
        if part.strip()
    ]
    if not paragraphs:
        paragraphs = [job["topic"]]
    return paragraphs[:5]


def _caption_timing(job: dict, duration: float) -> list[tuple[str, float, float]]:
    cues = job.get("caption_cues")
    valid = [
        cue
        for cue in cues
        if isinstance(cue, dict) and isinstance(cue.get("text"), str)
    ] if isinstance(cues, list) else []
    if not valid:
        texts = _scene_text(job)
        span = duration / len(texts)
        return [(text, index * span, (index + 1) * span) for index, text in enumerate(texts)]

    original_end = max(float(cue.get("end_seconds", 0)) for cue in valid)
    scale = duration / original_end if original_end > 0 else 1.0
    return [
        (
            " ".join(cue["text"].split()),
            max(0.0, float(cue.get("start_seconds", 0)) * scale),
            min(duration, float(cue.get("end_seconds", duration)) * scale),
        )
        for cue in valid
    ]


def _audio_duration(ffprobe: str | None, audio_path: Path | None) -> float | None:
    if not ffprobe or not audio_path or not audio_path.is_file():
        return None
    result = subprocess.run(
        [
            ffprobe, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        measured = float(result.stdout.strip())
    except ValueError:
        return None
    return measured if measured > 0 else None


def _has_audio_stream(ffprobe: str, video_path: Path) -> bool:
    result = subprocess.run(
        [
            ffprobe, "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_type",
            "-of", "default=noprint_wrappers=1:nokey=1", str(video_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and "audio" in result.stdout.splitlines()


def _filter_path(value: Path) -> str:
    return str(value.resolve()).replace("\\", "/").replace(":", r"\:")


def _ffmpeg_error_detail(stderr: str) -> str:
    detail = stderr.strip()
    if len(detail) > 1000:
        return f"{detail[:700]} ... {detail[-300:]}"
    return detail


def _create_storyboard_images(job: dict, output_dir: Path) -> list[Path]:
    from PIL import Image, ImageDraw, ImageFont

    width, height = 720, 1280
    root = (f"{job.get('topic', '')} {job.get('script', '')}").lower()
    cyber = any(
        marker in root
        for marker in ("साइबर", "फिशिंग", "phishing", "cybersecurity", "cyber security", "otp")
    )
    labels = (
        [
            ("THINK BEFORE YOU TAP", "A message can look real and still be fake."),
            ("CHECK THE DOMAIN", "Small spelling changes matter."),
            ("KEEP OTP PRIVATE", "Never share a code or PIN."),
            ("VERIFY. THEN ACT.", "Open the official app yourself."),
        ]
        if cyber
        else [
            ("THE BIG IDEA", "One clear question starts the story."),
            ("LOOK CLOSER", "Notice the detail most people miss."),
            ("HOW IT WORKS", "Follow the evidence, step by step."),
            ("THE TAKEAWAY", "Keep the lesson. Share your view."),
        ]
    )
    regular_font = next(
        (
            candidate
            for candidate in (
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
            )
            if Path(candidate).is_file()
        ),
        None,
    )
    bold_font = next(
        (
            candidate
            for candidate in (
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
            )
            if Path(candidate).is_file()
        ),
        regular_font,
    )
    regular = ImageFont.truetype(regular_font, 20) if regular_font else ImageFont.load_default()
    title_font = ImageFont.truetype(bold_font, 36) if bold_font else ImageFont.load_default()
    number_font = ImageFont.truetype(bold_font, 26) if bold_font else ImageFont.load_default()
    palette = [
        ("#38e0d0", "#ff6878"),
        ("#55c8ff", "#ffb84d"),
        ("#b29aff", "#ff6878"),
        ("#38e0d0", "#a4efbb"),
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for index, (headline, subtitle) in enumerate(labels):
        image = Image.new("RGB", (width, height))
        draw = ImageDraw.Draw(image)
        top = (5, 15, 29)
        bottom = (10, 37, 54)
        for y in range(height):
            ratio = y / height
            color = tuple(round(top[c] * (1 - ratio) + bottom[c] * ratio) for c in range(3))
            draw.line((0, y, width, y), fill=color)
        for x in range(30, width, 48):
            draw.line((x, 0, x, height), fill="#10283a", width=1)
        for y in range(30, height, 48):
            draw.line((0, y, width, y), fill="#10283a", width=1)

        accent, warning = palette[index]
        draw.rounded_rectangle(
            (34, 36, width - 34, height - 36),
            radius=32,
            outline="#25465b",
            width=2,
        )
        draw.text((64, 76), "ELITE EXPLAINER  /  DIGITAL SAFETY", font=regular, fill="#b9c9d6")
        draw.text((width - 100, 72), f"0{index + 1}", font=number_font, fill=accent)
        draw.rounded_rectangle((62, 160, width - 62, 930), radius=34, fill="#0d2032", outline="#1c3c52", width=2)
        draw.rounded_rectangle((92, 192, 230, 238), radius=18, fill="#18374a")
        draw.text((111, 202), "FIELD NOTE", font=regular, fill=accent)

        if cyber and index == 0:
            draw.rounded_rectangle((230, 310, 490, 715), radius=34, fill="#102b3e", outline=accent, width=5)
            draw.rounded_rectangle((252, 350, 468, 615), radius=20, fill="#081725")
            draw.rounded_rectangle((280, 390, 440, 492), radius=16, fill="#18384b")
            draw.ellipse((394, 408, 424, 438), fill=warning)
            draw.text((407, 405), "!", font=number_font, fill="#061522", anchor="mm")
            draw.text((282, 520), "NEW MESSAGE", font=regular, fill="#d9e7ef")
            draw.rounded_rectangle((273, 650, 448, 694), radius=18, fill="#48212d")
            draw.text((360, 672), "SUSPICIOUS LINK", font=regular, fill="#ffd7dc", anchor="mm")
            draw.ellipse((162, 350, 226, 414), outline=warning, width=5)
            draw.line((208, 400, 245, 438), fill=warning, width=8)
        elif cyber and index == 1:
            draw.rounded_rectangle((112, 338, 608, 640), radius=28, fill="#091827", outline="#42647a", width=3)
            draw.rounded_rectangle((112, 338, 608, 402), radius=28, fill="#18354a")
            for dot, color in zip((148, 182, 216), (warning, "#ffc857", accent)):
                draw.ellipse((dot - 9, 363, dot + 9, 381), fill=color)
            draw.rounded_rectangle((146, 440, 572, 526), radius=18, fill="#102c3f")
            draw.text((170, 462), "bank-alert.example", font=number_font, fill="#ffb5bd")
            draw.rounded_rectangle((146, 554, 518, 604), radius=15, fill="#173c46")
            draw.text((170, 566), "CHECK EVERY LETTER", font=regular, fill=accent)
            draw.ellipse((465, 666, 555, 756), outline=accent, width=7)
            draw.line((535, 735, 585, 786), fill=accent, width=10)
        elif cyber and index == 2:
            draw.rounded_rectangle((208, 315, 512, 730), radius=38, fill="#112a3e", outline="#b29aff", width=5)
            draw.rounded_rectangle((238, 364, 482, 650), radius=24, fill="#091725")
            draw.text((360, 405), "ONE-TIME CODE", font=regular, fill="#aebfce", anchor="mm")
            for dot_x in (284, 322, 360, 398, 436):
                draw.ellipse((dot_x - 8, 460, dot_x + 8, 476), fill="#b29aff")
            draw.rounded_rectangle((278, 535, 442, 590), radius=18, fill="#292443")
            draw.text((360, 562), "DO NOT SHARE", font=regular, fill="#e1d7ff", anchor="mm")
            draw.arc((294, 665, 426, 793), 180, 360, fill=accent, width=12)
            draw.rounded_rectangle((278, 724, 442, 838), radius=24, fill="#123d43", outline=accent, width=5)
            draw.ellipse((350, 762, 370, 782), fill="#e7fffb")
            draw.rounded_rectangle((356, 777, 364, 808), radius=4, fill="#e7fffb")
        elif cyber and index == 3:
            shield = [(360, 330), (550, 402), (525, 615), (360, 755), (195, 615), (170, 402)]
            draw.polygon(shield, fill="#123b44", outline=accent)
            draw.line([(270, 520), (335, 585), (458, 450)], fill="#e0fff8", width=20, joint="curve")
            for row, label in enumerate(("CHECK SENDER", "VERIFY DOMAIN", "PROTECT OTP")):
                y = 790 + row * 64
                draw.ellipse((156, y, 188, y + 32), outline=accent, width=3)
                draw.line((164, y + 16, 173, y + 24, 183, y + 8), fill=accent, width=4)
                draw.text((212, y + 2), label, font=regular, fill="#e3edf3")
        else:
            center = (width // 2, 530)
            draw.ellipse((center[0] - 140, center[1] - 140, center[0] + 140, center[1] + 140), outline=accent, width=8)
            draw.ellipse((center[0] - 95, center[1] - 95, center[0] + 95, center[1] + 95), fill="#143346")
            draw.line((center[0] - 42, center[1], center[0] - 8, center[1] + 36, center[0] + 62, center[1] - 44), fill="#e0fff8", width=16, joint="curve")
            for row, label in enumerate(("NOTICE", "UNDERSTAND", "REMEMBER")):
                y = 760 + row * 62
                draw.rounded_rectangle((160, y, 560, y + 42), radius=18, fill="#17384b")
                draw.text((190, y + 8), label, font=regular, fill="#e3edf3")

        draw.text((64, 982), headline, font=title_font, fill="#f3f7fa")
        draw.text((64, 1040), subtitle, font=regular, fill="#b9c9d6")
        draw.rounded_rectangle((64, 1128, 324, 1172), radius=18, fill="#18384a")
        draw.text((88, 1138), "PAUSE  /  CHECK  /  VERIFY", font=regular, fill=accent)
        destination = output_dir / f"{job['id']}-scene-{index + 1}.png"
        image.save(destination, format="PNG", optimize=True)
        paths.append(destination)
    return paths


def _validate_factory_url(value: str) -> str:
    try:
        url = httpx.URL(value.strip())
    except httpx.InvalidURL as exc:
        raise ValueError("FACTORY_URL must be an absolute HTTP(S) URL.") from exc
    if url.scheme not in {"http", "https"} or not url.host:
        raise ValueError("FACTORY_URL must be an absolute HTTP(S) URL.")
    if url.username or url.password or url.query or url.fragment:
        raise ValueError("FACTORY_URL must not contain credentials, query, or fragment.")
    if url.scheme == "http" and url.host not in {"127.0.0.1", "localhost", "::1", "[::1]"}:
        raise ValueError("FACTORY_URL must use HTTPS unless it points to localhost.")
    return str(url).rstrip("/")


def _extract_job(payload: object) -> dict | None:
    if not isinstance(payload, dict) or "job" not in payload:
        raise ValueError("The API returned an invalid worker response.")
    job = payload["job"]
    if job is None:
        return None
    if (
        not isinstance(job, dict)
        or not isinstance(job.get("id"), str)
        or not isinstance(job.get("topic"), str)
        or not isinstance(job.get("assets", {}), dict)
    ):
        raise ValueError("The API returned an invalid render job.")
    try:
        UUID(job["id"])
    except ValueError as exc:
        raise ValueError("The API returned an invalid render job ID.") from exc
    for asset_name in job.get("assets", {}).values():
        if (
            not isinstance(asset_name, str)
            or Path(asset_name).name != asset_name
            or "/" in asset_name
            or "\\" in asset_name
        ):
            raise ValueError("The API returned an unsafe render asset name.")
    thumbnail_names = job.get("thumbnail_names", {})
    if not isinstance(thumbnail_names, dict):
        raise ValueError("The API returned invalid thumbnail names.")
    for platform, filename in thumbnail_names.items():
        if (
            platform not in {"youtube", "instagram"}
            or not isinstance(filename, str)
            or Path(filename).name != filename
            or "/" in filename
            or "\\" in filename
            or not filename.endswith(".jpg")
        ):
            raise ValueError("The API returned an unsafe thumbnail filename.")
    return job


def render_video(
    job: dict[str, str],
    output_dir: Path,
    image_path: Path | None = None,
    audio_path: Path | None = None,
    scene_images: list[Path] | None = None,
    scene_videos: list[Path] | None = None,
) -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required on the render worker.")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{job['id']}.mp4"
    ffprobe = shutil.which("ffprobe")
    duration = max(15, min(90, int(job.get("duration_seconds") or 60)))
    measured_audio = _audio_duration(ffprobe, audio_path)
    if measured_audio is not None:
        duration = max(15, min(90, round(measured_audio)))
    if job.get("format") == "instagram_reel":
        duration = max(45, min(60, duration))
    font = job.get("font_path") or "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf"
    font_option = f":fontfile='{_escape_font_path(font)}'" if Path(font).is_file() else ""
    text_dir = output_dir / f"{job['id']}-captions"
    text_dir.mkdir(parents=True, exist_ok=True)
    if scene_videos is not None:
        if len(scene_videos) != 6 or any(not path.is_file() for path in scene_videos):
            raise RuntimeError("All six generated MiniMax scene videos are required.")
        scene_paths = scene_videos
        scene_is_video = True
    elif scene_images is not None:
        if len(scene_images) != 6 or any(not path.is_file() for path in scene_images):
            raise RuntimeError("All six generated scene images are required for this video.")
        scene_paths = scene_images
        scene_is_video = False
    else:
        scene_paths = (
            [image_path]
            if image_path and image_path.is_file()
            else _create_storyboard_images(job, output_dir)
        )
        scene_is_video = False
    if not scene_paths:
        raise RuntimeError("No illustration scenes were created for this video.")
    scene_duration = duration / len(scene_paths)
    cues = _caption_timing(job, duration)
    segments = []
    for scene_index, scene_path in enumerate(scene_paths):
        segment_start = scene_index * scene_duration
        segment_end = (scene_index + 1) * scene_duration
        filters = [
            "scale=780:1386:force_original_aspect_ratio=increase",
            "crop=720:1280:x='25+15*sin(t*0.25)':y='53+20*cos(t*0.2)'",
            "setsar=1",
            "format=yuv420p",
        ]
        for cue_index, (caption, start, end) in enumerate(cues):
            local_start = max(0.0, start - segment_start)
            local_end = min(scene_duration, end - segment_start)
            if local_end <= local_start:
                continue
            caption_path = text_dir / f"scene-{scene_index}-caption-{cue_index}.txt"
            caption_path.write_text(caption, encoding="utf-8")
            filters.extend(
                [
                    f"drawbox=x=30:y=ih-300:w=iw-60:h=220:"
                    f"color=black@0.72:t=fill:enable='between(t,{local_start:.3f},{local_end:.3f})'",
                    f"drawtext=textfile='{_filter_path(caption_path)}':"
                    f"fontcolor=white:fontsize=36:line_spacing=10:"
                    "x=(w-text_w)/2:y=(h-text_h)/2:"
                    f"enable='between(t,{local_start:.3f},{local_end:.3f})'{font_option}",
                ]
            )
        filters.append("drawbox=x=16:y=16:w=iw-32:h=ih-32:color=0x38e0d0@0.12:t=2")
        segment_path = output_dir / f"{job['id']}-segment-{scene_index + 1}.mp4"
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostats",
            "-filter_threads",
            "1",
            "-threads",
            "1",
            "-y",
        ]
        if scene_is_video:
            command.extend(["-stream_loop", "-1", "-i", str(scene_path)])
        else:
            command.extend([
                "-loop",
                "1",
                "-framerate",
                "24",
                "-t",
                f"{scene_duration:.3f}",
                "-i",
                str(scene_path),
            ])
        command.extend([
            "-vf",
            ",".join(filters),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "26",
            "-threads:v",
            "1",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "24",
            "-t",
            f"{scene_duration:.3f}",
            "-movflags",
            "+faststart",
            str(segment_path),
        ])
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        segment_exists = segment_path.is_file()
        segment_size = segment_path.stat().st_size if segment_exists else 0
        if completed.returncode != 0 or segment_size < 1024:
            detail = _ffmpeg_error_detail(completed.stderr)
            diagnostic = (
                f"exit={completed.returncode}, output_exists={segment_exists}, "
                f"output_size={segment_size} bytes"
            )
            if detail:
                diagnostic = f"{diagnostic}; {detail}"
            raise RuntimeError(
                f"FFmpeg scene {scene_index + 1} render failed: {diagnostic}"
            )
        segments.append(segment_path)

    concat_path = output_dir / f"{job['id']}-segments.txt"
    concat_path.write_text(
        "".join(f"file '{path.resolve().as_posix()}'\n" for path in segments),
        encoding="utf-8",
    )
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostats",
        "-filter_threads",
        "1",
        "-threads",
        "1",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
    ]
    if audio_path and audio_path.is_file():
        command.extend(["-i", str(audio_path), "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "128k"])
    else:
        command.extend(["-map", "0:v:0", "-an"])
    command.extend(
        [
            "-c:v",
            "copy",
            "-t",
            str(duration),
            "-movflags",
            "+faststart",
        ]
    )
    if audio_path and audio_path.is_file():
        command.append("-shortest")
    command.append(str(output_path))
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    output_exists = output_path.is_file()
    output_size = output_path.stat().st_size if output_exists else 0
    if completed.returncode != 0 or output_size < 1024:
        detail = _ffmpeg_error_detail(completed.stderr)
        diagnostic = (
            f"exit={completed.returncode}, output_exists={output_exists}, "
            f"output_size={output_size} bytes"
        )
        if detail:
            diagnostic = f"{diagnostic}; {detail}"
        raise RuntimeError(f"FFmpeg render failed: {diagnostic}")
    if audio_path and audio_path.is_file():
        if not ffprobe:
            raise RuntimeError("FFprobe is required to verify the generated narration track.")
        if not _has_audio_stream(ffprobe, output_path):
            raise RuntimeError("FFmpeg render completed without the required narration audio track.")
    return str(output_path)


def render_thumbnails(job: dict, video_path: Path, output_dir: Path) -> dict[str, Path]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required to generate platform thumbnails.")
    names = job.get("thumbnail_names")
    if not isinstance(names, dict):
        raise ValueError("The API did not provide safe thumbnail filenames.")
    title = " ".join(str(job.get("title") or job["topic"]).split())[:64]
    title_file = output_dir / f"{job['id']}-thumbnail-title.txt"
    title_file.write_text(title, encoding="utf-8")
    font = next(
        (
            candidate
            for candidate in (
                "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
                "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
                r"C:\Windows\Fonts\NirmalaB.ttf",
                r"C:\Windows\Fonts\NirmalaUI.ttf",
            )
            if Path(candidate).exists()
        ),
        None,
    )
    font_option = f":fontfile='{_escape_font_path(font)}'" if font else ""
    outputs = {}
    dimensions = {
        "youtube": (1280, 720, 48),
        "instagram": (1080, 1920, 64),
    }
    for platform, (width, height, fontsize) in dimensions.items():
        filename = names.get(platform)
        if not isinstance(filename, str) or Path(filename).name != filename:
            continue
        if not filename.lower().endswith(".jpg"):
            raise ValueError("The API returned an invalid thumbnail filename.")
        destination = output_dir / filename
        title_path = _filter_path(title_file)
        video_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},"
            f"drawbox=x=0:y=ih*0.62:w=iw:h=ih*0.38:color=black@0.68:t=fill,"
            "drawtext="
            f"textfile='{title_path}':fontcolor=white:fontsize={fontsize}:"
            f"line_spacing=10:x=(w-text_w)/2:y=h*0.69{font_option}"
        )
        command = [
            ffmpeg,
            "-y",
            "-ss",
            "1",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-vf",
            video_filter,
            "-q:v",
            "3",
            str(destination),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if (
            completed.returncode != 0
            or not destination.is_file()
            or destination.stat().st_size < 1024
        ):
            detail = completed.stderr[-1000:].strip()
            raise RuntimeError(f"{platform.title()} thumbnail generation failed: {detail or 'no output was created'}")
        outputs[platform] = destination
    expected_platforms = set(names)
    if set(outputs) != expected_platforms:
        raise ValueError("The API requested an unsupported thumbnail platform.")
    return outputs


def run(base_url: str, token: str, poll_seconds: int, output_dir: Path) -> None:
    worker_id = _configured_worker_id()
    headers = {"X-Worker-Token": token}
    timeout = httpx.Timeout(300.0, connect=20.0)
    retry_seconds = max(5, poll_seconds)
    with httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout) as client:
        while True:
            try:
                heartbeat = client.post(
                    "/api/worker/heartbeat",
                    params={"worker_id": worker_id},
                )
                heartbeat.raise_for_status()
                response = client.post(
                    "/api/worker/claim",
                    params={"worker_id": worker_id},
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                print(
                    f"API connection failed ({type(exc).__name__}); "
                    f"retrying in {retry_seconds} seconds",
                    flush=True,
                )
                time.sleep(retry_seconds)
                retry_seconds = min(retry_seconds * 2, 300)
                continue

            retry_seconds = max(5, poll_seconds)
            try:
                job = _extract_job(response.json())
            except ValueError as exc:
                print(f"Invalid worker response ({type(exc).__name__}); retrying.", flush=True)
                time.sleep(max(5, poll_seconds))
                continue
            if job:
                print(f"Claimed {job['id']} · {job['topic']}", flush=True)
                job_work_dir = output_dir / job["id"]
                try:
                    job_work_dir.mkdir(parents=True, exist_ok=True)
                    assets = job.get("assets") or {}
                    local_assets = {}
                    for kind, name in assets.items():
                        destination = job_work_dir / name
                        response = client.get(f"/api/worker/{job['id']}/asset/{name}")
                        response.raise_for_status()
                        destination.write_bytes(response.content)
                        local_assets[kind] = destination
                    print(
                        f"Render inputs for {job['id']}: "
                        f"{ {kind: path.stat().st_size for kind, path in local_assets.items()} }",
                        flush=True,
                    )
                    render_path = render_video(
                        job,
                        job_work_dir,
                        local_assets.get("image"),
                        local_assets.get("voiceover"),
                        [
                            local_assets[f"scene_{index}"]
                            for index in range(1, 7)
                            if f"scene_{index}" in local_assets
                        ] or None,
                        [
                            local_assets[f"scene_video_{index}"]
                            for index in range(1, 7)
                            if f"scene_video_{index}" in local_assets
                        ] or None,
                    )
                    thumbnails = render_thumbnails(job, Path(render_path), job_work_dir)
                    for platform, thumbnail_path in thumbnails.items():
                        with thumbnail_path.open("rb") as thumbnail:
                            uploaded_thumbnail = client.post(
                                f"/api/worker/{job['id']}/thumbnail/{platform}",
                                files={"file": (thumbnail_path.name, thumbnail, "image/jpeg")},
                            )
                        uploaded_thumbnail.raise_for_status()
                    with Path(render_path).open("rb") as media:
                        uploaded = client.post(
                            f"/api/worker/{job['id']}/media",
                            files={"file": (Path(render_path).name, media, "video/mp4")},
                        )
                    uploaded.raise_for_status()
                    render_path = uploaded.json()["render_path"]
                    completed = client.post(
                        f"/api/worker/{job['id']}/complete",
                        params={"worker_id": worker_id, "render_path": render_path},
                    )
                    completed.raise_for_status()
                    print(f"Completed {job['id']}", flush=True)
                except Exception as exc:
                    print(f"Failed {job['id']}: {exc}", flush=True)
                    try:
                        failed = client.post(
                            f"/api/worker/{job['id']}/fail",
                            params={
                                "worker_id": worker_id,
                                "error_message": f"{type(exc).__name__}: {str(exc)[:900]}",
                            },
                        )
                        failed.raise_for_status()
                    except httpx.HTTPError as report_error:
                        print(
                            "Could not report the failed render to the API "
                            f"({type(report_error).__name__}); its render lease "
                            "will expire for later retry.",
                            flush=True,
                        )
                finally:
                    try:
                        shutil.rmtree(job_work_dir)
                    except FileNotFoundError:
                        pass
                    except OSError as cleanup_error:
                        print(
                            f"Could not remove temporary files for {job['id']} "
                            f"({type(cleanup_error).__name__})",
                            flush=True,
                        )
            else:
                time.sleep(poll_seconds)
                continue
            time.sleep(poll_seconds)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hindi Content Factory render worker")
    parser.add_argument("--url", default=os.environ.get("FACTORY_URL", ""))
    parser.add_argument("--token", default=os.environ.get("WORKER_TOKEN", ""))
    parser.add_argument("--token-file", default=os.environ.get("WORKER_TOKEN_FILE", ""))
    parser.add_argument("--poll-seconds", type=int, default=int(os.environ.get("WORKER_POLL_SECONDS", "30")))
    parser.add_argument("--output-dir", default=os.environ.get("WORKER_OUTPUT_DIR", "/tmp/factory-media"))
    args = parser.parse_args()
    if args.token_file:
        token_path = Path(args.token_file)
        if token_path.exists():
            # Windows PowerShell may write UTF-8 files with a BOM.
            args.token = token_path.read_text(encoding="utf-8-sig").strip()
    if not args.token:
        raise SystemExit("WORKER_TOKEN is required")
    try:
        _configured_worker_id()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    try:
        args.url = _validate_factory_url(args.url)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if any(ord(char) < 33 or ord(char) > 126 for char in args.token):
        raise SystemExit("WORKER_TOKEN contains unsupported characters; re-enter the Railway token exactly.")
    run(args.url, args.token, max(5, args.poll_seconds), Path(args.output_dir))
