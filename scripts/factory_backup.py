"""Interactively create or restore encrypted, portable factory backups."""

import getpass
import os
from pathlib import Path
import secrets
import tempfile
from urllib.parse import urlparse

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

MAGIC = b"HCFB1"
SALT_BYTES = 16
NONCE_BYTES = 12
TAG_BYTES = 16
KDF_ITERATIONS = 600_000
CHUNK_BYTES = 1024 * 1024


def _backup_key(passphrase: str, salt: bytes) -> bytes:
    return PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=KDF_ITERATIONS,
    ).derive(passphrase.encode("utf-8"))


def _prompt_url() -> str:
    value = input("Factory API HTTPS URL: ").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("Use the Railway HTTPS domain; secrets will not be sent over plain HTTP.")
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ValueError("Enter a valid factory API URL.")
    return value


def _prompt_admin_token() -> str:
    token = getpass.getpass("Railway ADMIN_API_TOKEN (hidden): ").strip()
    if not token:
        raise ValueError("ADMIN_API_TOKEN cannot be empty.")
    return token


def _prompt_backup_passphrase(confirm: bool = False) -> str:
    password = getpass.getpass("Backup encryption passphrase (16+ characters): ")
    if len(password) < 16:
        raise ValueError("Use a passphrase of at least 16 characters.")
    if confirm and password != getpass.getpass("Repeat backup passphrase: "):
        raise ValueError("Passphrases did not match.")
    return password


def _encrypt_response(response: httpx.Response, destination: Path, passphrase: str) -> None:
    salt = secrets.token_bytes(SALT_BYTES)
    nonce = secrets.token_bytes(NONCE_BYTES)
    encryptor = Cipher(
        algorithms.AES(_backup_key(passphrase, salt)),
        modes.GCM(nonce),
    ).encryptor()
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        with temporary.open("wb") as output:
            output.write(MAGIC + salt + nonce)
            for chunk in response.iter_bytes(CHUNK_BYTES):
                if chunk:
                    output.write(encryptor.update(chunk))
            output.write(encryptor.finalize())
            output.write(encryptor.tag)
        if os.name != "nt":
            temporary.chmod(0o600)
        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _decrypt_backup(source_path: Path, destination: Path, passphrase: str) -> None:
    temporary = destination.with_name(destination.name + ".tmp")
    header_bytes = len(MAGIC) + SALT_BYTES + NONCE_BYTES
    try:
        with source_path.open("rb") as source:
            header = source.read(header_bytes)
            if len(header) != header_bytes or not header.startswith(MAGIC):
                raise ValueError("This is not a supported factory backup file.")
            salt_start = len(MAGIC)
            salt = header[salt_start : salt_start + SALT_BYTES]
            nonce = header[salt_start + SALT_BYTES :]
            source.seek(0, os.SEEK_END)
            file_size = source.tell()
            ciphertext_size = file_size - header_bytes - TAG_BYTES
            if ciphertext_size < 1:
                raise ValueError("The backup file is truncated.")
            source.seek(-TAG_BYTES, os.SEEK_END)
            tag = source.read(TAG_BYTES)
            source.seek(header_bytes)
            decryptor = Cipher(
                algorithms.AES(_backup_key(passphrase, salt)),
                modes.GCM(nonce, tag),
            ).decryptor()
            remaining = ciphertext_size
            with temporary.open("wb") as output:
                while remaining:
                    chunk = source.read(min(CHUNK_BYTES, remaining))
                    if not chunk:
                        raise ValueError("The backup file is truncated.")
                    remaining -= len(chunk)
                    output.write(decryptor.update(chunk))
                output.write(decryptor.finalize())
        temporary.replace(destination)
    except InvalidTag as exc:
        temporary.unlink(missing_ok=True)
        raise ValueError("Wrong passphrase or damaged backup file.") from exc
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _backup() -> None:
    base_url = _prompt_url()
    token = _prompt_admin_token()
    passphrase = _prompt_backup_passphrase(confirm=True)
    default_dir = Path.home() / "Downloads"
    default_dir.mkdir(parents=True, exist_ok=True)
    entered_path = input(f"Encrypted backup file [{default_dir / 'factory-backup.hcfb'}]: ").strip()
    destination = Path(entered_path).expanduser() if entered_path else default_dir / "factory-backup.hcfb"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError("That backup file already exists; choose a different filename.")

    timeout = httpx.Timeout(600, connect=30)
    try:
        with httpx.Client(timeout=timeout) as client:
            with client.stream(
                "GET",
                f"{base_url}/api/backup/export",
                headers={"X-Admin-Token": token},
            ) as response:
                response.raise_for_status()
                _encrypt_response(response, destination, passphrase)
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"Factory backup endpoint returned HTTP {exc.response.status_code}.") from None
    except httpx.RequestError as exc:
        raise RuntimeError(f"Could not reach the factory API ({type(exc).__name__}).") from None
    print(f"Encrypted backup saved: {destination}")
    print("Keep the passphrase separately; it cannot be recovered if lost.")


def _restore() -> None:
    source_path = Path(input("Path to encrypted .hcfb backup: ").strip().strip('"')).expanduser()
    if not source_path.is_file():
        raise FileNotFoundError("Backup file was not found.")
    base_url = _prompt_url()
    token = _prompt_admin_token()
    passphrase = _prompt_backup_passphrase()
    confirm = input(
        "Restore overwrites only an EMPTY factory database and media directory. "
        "Enable BACKUP_RESTORE_ENABLED on the new Railway API first. Type RESTORE to continue: "
    ).strip()
    if confirm != "RESTORE":
        print("Restore cancelled.")
        return

    with tempfile.TemporaryDirectory(prefix="factory-restore-") as directory:
        archive_path = Path(directory) / "factory-backup.tar.gz"
        _decrypt_backup(source_path, archive_path, passphrase)
        timeout = httpx.Timeout(600, connect=30)
        try:
            with httpx.Client(timeout=timeout) as client, archive_path.open("rb") as archive:
                response = client.post(
                    f"{base_url}/api/backup/restore",
                    headers={"X-Admin-Token": token},
                    files={"backup": ("factory-backup.tar.gz", archive, "application/gzip")},
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                raise RuntimeError(
                    "Restore is disabled. Set BACKUP_RESTORE_ENABLED=true on the new Railway API and retry."
                ) from None
            raise RuntimeError(f"Factory restore endpoint returned HTTP {exc.response.status_code}.") from None
        except httpx.RequestError as exc:
            raise RuntimeError(f"Could not reach the factory API ({type(exc).__name__}).") from None
        print(f"Restore complete: {response.json()}")
        print("Disable BACKUP_RESTORE_ENABLED now, then verify the factory remains paused and mock-only.")


def main() -> int:
    print("Hindi Content Factory — encrypted migration backup")
    print("1) Create an encrypted backup")
    print("2) Restore to a new, empty Railway factory")
    choice = input("Choose 1 or 2: ").strip()
    try:
        if choice == "1":
            _backup()
        elif choice == "2":
            _restore()
        else:
            print("Choose 1 or 2.")
            return 2
    except (ValueError, FileNotFoundError, FileExistsError, RuntimeError) as exc:
        print(f"Backup operation stopped: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
