"""Portable application-data archives for Railway account migrations."""

import base64
from datetime import date, datetime, time
from io import BytesIO
import json
import logging
from pathlib import Path, PurePosixPath
import tempfile
import tarfile
from typing import Any

from sqlalchemy import func, select

from .config import get_settings
from .db import Base, SessionLocal

BACKUP_FORMAT_VERSION = 1
REQUIRED_MIGRATION_VARIABLES = (
    "APP_PAUSED",
    "MOCK_PUBLISHING",
    "AUTOMATION_ENABLED",
    "TOKEN_ENCRYPTION_KEY",
    "ADMIN_API_TOKEN",
    "WORKER_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_OWNER_IDS",
    "TELEGRAM_MODE",
    "PUBLIC_BASE_URL",
    "MEDIA_ROOT",
    "YOUTUBE_CLIENT_ID",
    "YOUTUBE_CLIENT_SECRET",
    "YOUTUBE_REDIRECT_URI",
    "YOUTUBE_ENABLED",
    "META_APP_ID",
    "META_APP_SECRET",
    "INSTAGRAM_USER_ID",
    "INSTAGRAM_ACCESS_TOKEN",
    "INSTAGRAM_ENABLED",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_SERVICE_ACCOUNT_JSON",
)
logger = logging.getLogger(__name__)


def _encode_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return {"__factory_type__": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"__factory_type__": "date", "value": value.isoformat()}
    if isinstance(value, time):
        return {"__factory_type__": "time", "value": value.isoformat()}
    if isinstance(value, bytes):
        return {"__factory_type__": "bytes", "value": base64.b64encode(value).decode("ascii")}
    return value


def _decode_value(value: Any) -> Any:
    if not isinstance(value, dict) or set(value) != {"__factory_type__", "value"}:
        return value
    kind = value["__factory_type__"]
    raw = value["value"]
    if kind == "datetime":
        return datetime.fromisoformat(raw)
    if kind == "date":
        return date.fromisoformat(raw)
    if kind == "time":
        return time.fromisoformat(raw)
    if kind == "bytes":
        return base64.b64decode(raw, validate=True)
    raise ValueError("Backup contains an unsupported encoded value")


def _database_snapshot() -> bytes:
    from .db import engine

    tables = {}
    with engine.connect() as connection:
        if connection.dialect.name == "postgresql":
            connection = connection.execution_options(isolation_level="REPEATABLE READ")
        with connection.begin():
            for table in Base.metadata.sorted_tables:
                rows = connection.execute(select(table)).mappings()
                tables[table.name] = [
                    {key: _encode_value(value) for key, value in row.items()}
                    for row in rows
                ]
    payload = {
        "format_version": BACKUP_FORMAT_VERSION,
        "tables": tables,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def create_backup_archive() -> Path:
    """Create an unencrypted tar.gz for the authenticated client to encrypt locally."""
    settings = get_settings()
    media_root = Path(settings.media_root).resolve()
    backup_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="factory-backup-", suffix=".tar.gz", delete=False) as handle:
            backup_path = Path(handle.name)
        with tarfile.open(backup_path, mode="w:gz") as archive:
            manifest = {
                "format_version": BACKUP_FORMAT_VERSION,
                "created_at": datetime.now().astimezone().isoformat(),
                "required_migration_variables": list(REQUIRED_MIGRATION_VARIABLES),
                "media_included": media_root.is_dir(),
            }
            for name, payload in (
                ("manifest.json", json.dumps(manifest, indent=2).encode("utf-8")),
                ("database.json", _database_snapshot()),
            ):
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                archive.addfile(info, fileobj=BytesIO(payload))
            if media_root.is_dir():
                for path in sorted(media_root.rglob("*")):
                    if path.is_symlink():
                        raise ValueError("Media directory contains a symbolic link; backup stopped safely.")
                    if path.is_file():
                        relative = path.relative_to(media_root).as_posix()
                        archive.add(path, arcname=f"media/{relative}", recursive=False)
        return backup_path
    except Exception:
        if backup_path is not None:
            backup_path.unlink(missing_ok=True)
        raise


def restore_backup_archive(archive_path: Path) -> dict[str, int]:
    """Restore a versioned archive only into a completely empty database and media directory."""
    media_root = Path(get_settings().media_root).resolve()
    created_files: list[Path] = []
    created_directories: list[Path] = []
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            members = archive.getmembers()
            max_bytes = get_settings().backup_max_mb * 1024 * 1024
            if sum(member.size for member in members if member.isfile()) > max_bytes:
                raise ValueError("Backup contents exceed BACKUP_MAX_MB.")
            manifest_member = _require_regular_member(archive, "manifest.json")
            database_member = _require_regular_member(archive, "database.json")
            manifest_file = archive.extractfile(manifest_member)
            database_file = archive.extractfile(database_member)
            if manifest_file is None or database_file is None:
                raise ValueError("Backup metadata could not be read.")
            manifest = json.load(manifest_file)
            database = json.load(database_file)
            if (
                not isinstance(manifest, dict)
                or not isinstance(database, dict)
                or manifest.get("format_version") != BACKUP_FORMAT_VERSION
                or database.get("format_version") != BACKUP_FORMAT_VERSION
            ):
                raise ValueError("Unsupported backup format version.")

            expected_tables = {table.name: table for table in Base.metadata.sorted_tables}
            supplied_tables = database.get("tables")
            if not isinstance(supplied_tables, dict) or set(supplied_tables) != set(expected_tables):
                raise ValueError("Backup database tables do not match this factory version.")

            media_members = []
            seen_names: set[str] = set()
            for member in members:
                if member.name in seen_names:
                    raise ValueError("Backup contains duplicate archive entries.")
                seen_names.add(member.name)
                if member.name in {"manifest.json", "database.json"}:
                    continue
                if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                    raise ValueError("Backup contains an unsupported file type.")
                member_path = PurePosixPath(member.name)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise ValueError("Backup contains an unsafe path.")
                if member_path.parts[0] != "media":
                    raise ValueError("Backup contains an unexpected archive path.")
                if member.isfile():
                    media_members.append(member)

            with SessionLocal() as session:
                with session.begin():
                    for table in expected_tables.values():
                        count = session.scalar(select(func.count()).select_from(table))
                        if count:
                            raise ValueError("Restore is allowed only into an empty factory database.")
                    if media_root.exists() and any(media_root.iterdir()):
                        raise ValueError("Restore is allowed only when the media directory is empty.")

                    for table_name, table in expected_tables.items():
                        rows = supplied_tables[table_name]
                        if not isinstance(rows, list):
                            raise ValueError("Backup table data is invalid.")
                        allowed_columns = {column.name for column in table.columns}
                        decoded_rows = []
                        for row in rows:
                            if not isinstance(row, dict) or not set(row).issubset(allowed_columns):
                                raise ValueError("Backup contains invalid table columns.")
                            decoded_rows.append(
                                {key: _decode_value(value) for key, value in row.items()}
                            )
                        if decoded_rows:
                            session.execute(table.insert(), decoded_rows)

                    media_root.mkdir(parents=True, exist_ok=True)
                    for member in media_members:
                        member_path = PurePosixPath(member.name)
                        relative = Path(*member_path.parts[1:])
                        target = (media_root / relative).resolve()
                        if target == media_root or media_root not in target.parents:
                            raise ValueError("Backup contains an unsafe media path.")
                        missing_directories = []
                        parent = target.parent
                        while parent != media_root and not parent.exists():
                            missing_directories.append(parent)
                            parent = parent.parent
                        target.parent.mkdir(parents=True, exist_ok=True)
                        created_directories.extend(reversed(missing_directories))
                        source = archive.extractfile(member)
                        if source is None:
                            raise ValueError("Backup media entry could not be read.")
                        created_files.append(target)
                        with source, target.open("wb") as destination:
                            while chunk := source.read(1024 * 1024):
                                destination.write(chunk)
        return {
            "tables": len(expected_tables),
            "rows": sum(len(rows) for rows in supplied_tables.values()),
            "media_files": len(created_files),
        }
    except (tarfile.TarError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        _cleanup_restore_files(created_files, created_directories)
        raise ValueError("Backup archive is invalid or damaged.") from exc
    except Exception:
        _cleanup_restore_files(created_files, created_directories)
        raise


def _cleanup_restore_files(files: list[Path], directories: list[Path]) -> None:
    for path in reversed(files):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove partial restore file %s", path)
    for path in reversed(directories):
        try:
            path.rmdir()
        except OSError:
            logger.warning("Could not remove partial restore directory %s", path)


def _require_regular_member(archive: tarfile.TarFile, name: str) -> tarfile.TarInfo:
    matches = [member for member in archive.getmembers() if member.name == name]
    if len(matches) != 1 or not matches[0].isfile():
        raise ValueError(f"Backup is missing a valid {name}.")
    return matches[0]
