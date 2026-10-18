import argparse
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import time

import httpx


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


def _filter_path(value: Path) -> str:
    return str(value.resolve()).replace("\\", "/").replace(":", r"\:")


def render_video(job: dict[str, str], output_dir: Path, image_path: Path | None = None, audio_path: Path | None = None) -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required on the RDP worker. Install FFmpeg and restart the worker.")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{job['id']}.mp4"
    duration = max(15, min(90, int(job.get("duration_seconds") or 60)))
    topic = _escape_drawtext(job["topic"][:180])
    font = job.get("font_path")
    if not font:
        for candidate in (
            r"C:\Windows\Fonts\NirmalaUI.ttf",
            r"C:\Windows\Fonts\arial.ttf",
            r"C:\Windows\Fonts\segoeui.ttf",
            r"C:\Windows\Fonts\calibri.ttf",
        ):
            if Path(candidate).exists():
                font = candidate
                break
    font_option = f":fontfile='{_escape_font_path(font)}'" if font and Path(font).exists() else ""
    scene_texts = _scene_text(job)
    scene_seconds = duration / len(scene_texts)
    text_dir = output_dir / f"{job['id']}-captions"
    text_dir.mkdir(parents=True, exist_ok=True)
    filters = [
        f"drawtext=text='{topic}':fontcolor=white:fontsize=60:x=(w-text_w)/2:y=180{font_option}"
    ]
    for index, caption in enumerate(scene_texts):
        caption_path = text_dir / f"scene-{index}.txt"
        caption_path.write_text(caption, encoding="utf-8")
        start = index * scene_seconds
        end = (index + 1) * scene_seconds
        filters.append(
            "drawbox=x=60:y=h-430:w=w-120:h=250:color=black@0.55:t=fill:"
            f"enable='between(t,{start:.3f},{end:.3f})'"
        )
        filters.append(
            "drawtext="
            f"textfile='{_filter_path(caption_path)}':fontcolor=white:fontsize=42:"
            "line_spacing=12:x=(w-text_w)/2:y=(h-text_h)/2:"
            f"enable='between(t,{start:.3f},{end:.3f})'{font_option}"
        )
    video_filter = ",".join(filters)
    inputs = []
    if image_path and image_path.exists():
        inputs.extend(["-loop", "1", "-i", str(image_path)])
    else:
        inputs.extend(["-f", "lavfi", "-i", f"color=c=0x111827:s=1080x1920:r=30:d={duration}"])
    if audio_path and audio_path.exists():
        inputs.extend(["-i", str(audio_path)])
    command = [ffmpeg, "-y", *inputs]
    if audio_path and audio_path.exists():
        command.extend(["-map", "0:v:0", "-map", "1:a:0"])
    command.extend(["-vf", video_filter, "-c:v", "libx264", "-pix_fmt", "yuv420p"])
    if audio_path and audio_path.exists():
        command.extend(["-c:a", "aac", "-t", str(duration)])
    else:
        command.append("-an")
    command.extend(["-movflags", "+faststart", str(output_path)])
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not output_path.exists() or output_path.stat().st_size < 1024:
        detail = completed.stderr[-1000:].strip()
        raise RuntimeError(f"FFmpeg render failed: {detail or 'no output was created'}")
    return str(output_path)


def run(base_url: str, token: str, poll_seconds: int, output_dir: Path) -> None:
    worker_id = socket.gethostname()
    headers = {"X-Worker-Token": token}
    timeout = httpx.Timeout(300.0, connect=20.0)
    with httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout) as client:
        while True:
            heartbeat = client.post("/api/worker/heartbeat", params={"worker_id": worker_id})
            heartbeat.raise_for_status()
            response = client.post("/api/worker/claim", params={"worker_id": worker_id})
            response.raise_for_status()
            payload = response.json()
            job = payload.get("job")
            if job:
                print(f"Claimed {job['id']} · {job['topic']}", flush=True)
                try:
                    assets = job.get("assets") or {}
                    local_assets = {}
                    for kind, name in assets.items():
                        destination = output_dir / name
                        response = client.get(f"/api/worker/{job['id']}/asset/{name}")
                        response.raise_for_status()
                        destination.write_bytes(response.content)
                        local_assets[kind] = destination
                    render_path = render_video(
                        job,
                        output_dir,
                        local_assets.get("image"),
                        local_assets.get("voiceover"),
                    )
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
                    failed = client.post(
                        f"/api/worker/{job['id']}/fail",
                        params={"worker_id": worker_id, "error_message": str(exc)[:1000]},
                    )
                    failed.raise_for_status()
                    print(f"Failed {job['id']}: {exc}", flush=True)
            else:
                time.sleep(poll_seconds)
                continue
            time.sleep(poll_seconds)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hindi Content Factory RDP worker")
    parser.add_argument("--url", default=os.environ.get("FACTORY_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--token", default=os.environ.get("WORKER_TOKEN", ""))
    parser.add_argument("--token-file", default=os.environ.get("WORKER_TOKEN_FILE", ""))
    parser.add_argument("--poll-seconds", type=int, default=int(os.environ.get("WORKER_POLL_SECONDS", "30")))
    parser.add_argument("--output-dir", default=os.environ.get("WORKER_OUTPUT_DIR", "media"))
    args = parser.parse_args()
    if args.token_file:
        token_path = Path(args.token_file)
        if token_path.exists():
            # Windows PowerShell may write UTF-8 files with a BOM.
            args.token = token_path.read_text(encoding="utf-8-sig").strip()
    if not args.token:
        raise SystemExit("WORKER_TOKEN is required")
    if any(ord(char) < 33 or ord(char) > 126 for char in args.token):
        raise SystemExit("WORKER_TOKEN contains unsupported characters; re-enter the Railway token exactly.")
    run(args.url, args.token, max(5, args.poll_seconds), Path(args.output_dir))
