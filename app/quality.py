"""Quality gate for publication.

Two levels of findings:

- **Blockers** stop publication outright: missing/broken essentials and
  hard platform limits (e.g. a YouTube title over 100 characters).
- **Warnings** never block, but lower the score and are shown to the
  operator: retention and monetisation polish — a weak hook, a missing
  discussion CTA, off-target duration, missing hashtags, storyboard,
  captions plan or AI disclosure. A video *can* publish with warnings;
  it just probably should not until they are fixed.
"""
import json
from dataclasses import dataclass, field

from .storyboard import TARGET_DURATION_SECONDS, script_structure

YOUTUBE_TITLE_MAX_CHARS = 100
_DURATION_TOLERANCE_SECONDS = 30


@dataclass(frozen=True)
class QualityReport:
    passed: bool
    score: int
    blockers: tuple[str, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)


def validate_content(*, topic: str, script: str | None, metadata_json: str | None, render_path: str | None) -> QualityReport:
    blockers: list[str] = []
    warnings: list[str] = []
    if not topic.strip():
        blockers.append("missing_topic")
    if not script or len(script.strip()) < 80:
        blockers.append("script_too_short")

    metadata: dict[str, object] | None = None
    if not metadata_json:
        blockers.append("missing_metadata")
    else:
        try:
            parsed = json.loads(metadata_json)
            metadata = parsed if isinstance(parsed, dict) else {}
            if not metadata.get("title") or not metadata.get("description"):
                blockers.append("incomplete_metadata")
        except json.JSONDecodeError:
            blockers.append("invalid_metadata")

    if metadata is not None:
        title = str(metadata.get("title") or "")
        if title and len(title) > YOUTUBE_TITLE_MAX_CHARS:
            blockers.append("title_too_long")
        if not metadata.get("hashtags"):
            warnings.append("missing_hashtags")
        if not metadata.get("storyboard"):
            warnings.append("missing_storyboard")
        if not metadata.get("disclosure"):
            warnings.append("missing_ai_disclosure")
        if not metadata.get("caption_cues"):
            warnings.append("missing_caption_cues")

    if script and len(script.strip()) >= 80:
        structure = script_structure(script)
        if not structure["has_hook"]:
            warnings.append("weak_hook")
        if not structure["has_discussion_cta"]:
            warnings.append("missing_discussion_cta")
        duration = float(structure["estimated_duration_seconds"])  # type: ignore[arg-type]
        # The job's own format decides the target (Shorts ~60s, Long ~240s);
        # metadata written by generate_content carries it.
        target = TARGET_DURATION_SECONDS
        if metadata is not None:
            try:
                target = float(metadata.get("target_duration_seconds") or target)
            except (TypeError, ValueError):
                pass
        tolerance = max(_DURATION_TOLERANCE_SECONDS, target * 0.25)
        if abs(duration - target) > tolerance:
            warnings.append("duration_off_target")

    if not render_path:
        blockers.append("missing_render")
    score = max(0, 100 - len(blockers) * 25 - len(warnings) * 5)
    return QualityReport(
        passed=not blockers,
        score=score,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )
