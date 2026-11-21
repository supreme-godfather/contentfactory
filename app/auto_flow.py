"""Resolve which genre a job should use, and audit what the engine did.

The owner picks the mode in Telegram:

- ``specific``  — one chosen genre
- ``shuffle``   — a random genre inside the chosen category
- ``random``    — a random genre from every category
- ``auto``      — the best match for the topic, chosen deterministically
"""
from datetime import datetime, timezone

from sqlalchemy import select

from .db import FactorySetting, SessionLocal


def resolve_genre(topic: str) -> tuple[dict[str, str], str]:
    """Return the genre for a topic plus a short reason for the audit log."""
    from .genres import get_genre, match_genre_for_topic, random_genre
    from .hq import get_genre_id, get_genre_mode

    mode = get_genre_mode()
    if mode == "specific":
        genre = get_genre(get_genre_id())
        if genre:
            return genre, "owner selected genre"
        mode = "auto"
    if mode == "shuffle":
        genre = random_genre()
        return genre, f"shuffled {genre['category']}"
    if mode == "random":
        genre = random_genre()
        return genre, "random genre"
    genre = match_genre_for_topic(topic)
    return genre, "matched to topic"


def record_automation_event(kind: str, job_id: str, detail: str) -> None:
    """Append a short, secret-free audit line for the auto pipeline."""
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    line = f"{stamp} | {kind} | {job_id} | {detail}"[:480]
    with SessionLocal() as session:
        setting = session.get(FactorySetting, "auto_activity_log")
        history = setting.value if setting and setting.value else ""
        history = f"{history}\n{line}".strip()
        if setting is None:
            session.add(FactorySetting(key="auto_activity_log", value=history[-4000:]))
        else:
            setting.value = history[-4000:]
        session.commit()


def recent_automation_events(limit: int = 12) -> list[str]:
    with SessionLocal() as session:
        setting = session.get(FactorySetting, "auto_activity_log")
    if not setting or not setting.value:
        return []
    lines = [line for line in setting.value.splitlines() if line.strip()]
    return lines[-limit:]
