import json
from dataclasses import dataclass


@dataclass(frozen=True)
class QualityReport:
    passed: bool
    score: int
    blockers: tuple[str, ...]


def validate_content(*, topic: str, script: str | None, metadata_json: str | None, render_path: str | None) -> QualityReport:
    blockers: list[str] = []
    if not topic.strip():
        blockers.append("missing_topic")
    if not script or len(script.strip()) < 80:
        blockers.append("script_too_short")
    if not metadata_json:
        blockers.append("missing_metadata")
    else:
        try:
            metadata = json.loads(metadata_json)
            if not metadata.get("title") or not metadata.get("description"):
                blockers.append("incomplete_metadata")
        except json.JSONDecodeError:
            blockers.append("invalid_metadata")
    if not render_path:
        blockers.append("missing_render")
    score = max(0, 100 - len(blockers) * 25)
    return QualityReport(passed=not blockers, score=score, blockers=tuple(blockers))
