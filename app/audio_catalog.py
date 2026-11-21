import json
import threading
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

_LOCK = threading.Lock()
_MAX_AUDIO_BYTES = 20 * 1024 * 1024
_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a"}


def _manifest_path(media_root: Path) -> Path:
    return media_root / "audio-catalog.json"


def _load(media_root: Path) -> dict[str, object]:
    path = _manifest_path(media_root)
    if not path.is_file():
        return {"version": 1, "entries": [], "defaults": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Audio catalog is unreadable.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        raise RuntimeError("Audio catalog is invalid.")
    payload.setdefault("defaults", {})
    return payload


def _save(media_root: Path, payload: dict[str, object]) -> None:
    media_root.mkdir(parents=True, exist_ok=True)
    path = _manifest_path(media_root)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _validate_audio(data: bytes, suffix: str) -> None:
    if not data or len(data) > _MAX_AUDIO_BYTES:
        raise ValueError("Audio file must be between 1 byte and 20 MB.")
    valid = (
        suffix == ".mp3" and (data.startswith(b"ID3") or data[:1] == b"\xff")
        or suffix == ".wav" and data.startswith(b"RIFF") and data[8:12] == b"WAVE"
        or suffix == ".ogg" and data.startswith(b"OggS")
        or suffix == ".m4a" and len(data) >= 12 and data[4:8] == b"ftyp"
    )
    if not valid:
        raise ValueError("Audio content does not match its supported file type.")


def register_audio_asset(
    media_root: Path,
    *,
    kind: str,
    original_name: str,
    data: bytes,
) -> dict[str, object]:
    if kind not in {"bgm", "sfx"}:
        raise ValueError("Audio kind must be bgm or sfx.")
    suffix = Path(original_name).suffix.lower()
    if suffix not in _EXTENSIONS:
        raise ValueError("Supported audio formats are MP3, WAV, OGG, and M4A.")
    _validate_audio(data, suffix)
    asset_id = uuid4().hex[:16]
    filename = f"audio-{kind}-{asset_id}{suffix}"
    destination = media_root / filename
    temporary = media_root / f".{filename}.tmp"
    media_root.mkdir(parents=True, exist_ok=True)
    temporary.write_bytes(data)
    temporary.replace(destination)

    entry: dict[str, object] = {
        "id": asset_id,
        "kind": kind,
        "filename": filename,
        "name": Path(original_name).name[:160],
        "sha256": sha256(data).hexdigest(),
        "bytes": len(data),
        "source": "telegram_owner_upload",
        "operator_managed": True,
    }
    with _LOCK:
        payload = _load(media_root)
        entries = payload["entries"]
        assert isinstance(entries, list)
        entries.append(entry)
        defaults = payload.setdefault("defaults", {})
        assert isinstance(defaults, dict)
        defaults.setdefault(kind, asset_id)
        _save(media_root, payload)
    return entry


def list_audio_assets(media_root: Path, kind: str | None = None) -> list[dict[str, object]]:
    with _LOCK:
        payload = _load(media_root)
    entries = [entry for entry in payload["entries"] if isinstance(entry, dict)]
    return [entry for entry in entries if kind is None or entry.get("kind") == kind]


def set_default_audio_asset(media_root: Path, kind: str, asset_id: str) -> None:
    with _LOCK:
        payload = _load(media_root)
        entries = payload["entries"]
        if not any(
            isinstance(entry, dict)
            and entry.get("id") == asset_id
            and entry.get("kind") == kind
            for entry in entries
        ):
            raise ValueError("Audio asset was not found.")
        defaults = payload.setdefault("defaults", {})
        assert isinstance(defaults, dict)
        defaults[kind] = asset_id
        _save(media_root, payload)


def get_default_audio_asset(media_root: Path, kind: str) -> dict[str, object] | None:
    with _LOCK:
        payload = _load(media_root)
    defaults = payload.get("defaults") or {}
    asset_id = defaults.get(kind) if isinstance(defaults, dict) else None
    for entry in payload["entries"]:
        if isinstance(entry, dict) and entry.get("id") == asset_id and entry.get("kind") == kind:
            filename = entry.get("filename")
            if isinstance(filename, str) and Path(filename).name == filename and (media_root / filename).is_file():
                return entry
    return None


def delete_audio_asset(media_root: Path, asset_id: str) -> bool:
    with _LOCK:
        payload = _load(media_root)
        entries = payload["entries"]
        match = next(
            (
                entry
                for entry in entries
                if isinstance(entry, dict) and entry.get("id") == asset_id
            ),
            None,
        )
        if match is None:
            return False
        filename = match.get("filename")
        payload["entries"] = [entry for entry in entries if entry is not match]
        defaults = payload.setdefault("defaults", {})
        assert isinstance(defaults, dict)
        for kind, selected_id in list(defaults.items()):
            if selected_id == asset_id:
                replacement = next(
                    (
                        entry.get("id")
                        for entry in payload["entries"]
                        if isinstance(entry, dict) and entry.get("kind") == kind
                    ),
                    None,
                )
                if replacement:
                    defaults[kind] = replacement
                else:
                    defaults.pop(kind, None)
        _save(media_root, payload)
    if isinstance(filename, str) and Path(filename).name == filename:
        (media_root / filename).unlink(missing_ok=True)
    return True
