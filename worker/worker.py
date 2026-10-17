import argparse
import json
import os
from pathlib import Path
import socket
import time

import httpx


def render_manifest(job: dict[str, str], output_dir: Path) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / f"{job['id']}.json"
    manifest_path.write_text(
        json.dumps(
            {
                "job_id": job["id"],
                "topic": job["topic"],
                "brand": job["brand"],
                "platform": job["platform"],
                "status": "rendered-manifest",
                "next": "replace with FFmpeg/TTS renderer",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return str(manifest_path)


def run(base_url: str, token: str, poll_seconds: int, output_dir: Path) -> None:
    worker_id = socket.gethostname()
    headers = {"X-Worker-Token": token}
    with httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=20) as client:
        while True:
            heartbeat = client.post("/api/worker/heartbeat", params={"worker_id": worker_id})
            heartbeat.raise_for_status()
            response = client.post("/api/worker/claim")
            response.raise_for_status()
            payload = response.json()
            job = payload.get("job")
            if job:
                print(f"Claimed {job['id']} · {job['topic']}", flush=True)
                render_path = render_manifest(job, output_dir)
                completed = client.post(
                    f"/api/worker/{job['id']}/complete",
                    params={"render_path": render_path},
                )
                completed.raise_for_status()
                print(f"Completed {job['id']}", flush=True)
            time.sleep(poll_seconds)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hindi Content Factory RDP worker")
    parser.add_argument("--url", default=os.environ.get("FACTORY_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--token", default=os.environ.get("WORKER_TOKEN", ""))
    parser.add_argument("--poll-seconds", type=int, default=int(os.environ.get("WORKER_POLL_SECONDS", "30")))
    parser.add_argument("--output-dir", default=os.environ.get("WORKER_OUTPUT_DIR", "media"))
    args = parser.parse_args()
    if not args.token:
        raise SystemExit("WORKER_TOKEN is required")
    run(args.url, args.token, max(5, args.poll_seconds), Path(args.output_dir))
