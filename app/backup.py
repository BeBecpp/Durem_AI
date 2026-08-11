from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import stat
import struct
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from .config import settings
from .db import connect, get_setting, init_db

MAGIC = b"DUREMENC1"
SALT_BYTES = 16
NONCE_BYTES = 12
TAG_BYTES = 16
KDF_ITERATIONS = 600_000
HEADER_LEN = len(MAGIC) + SALT_BYTES + NONCE_BYTES + 4
CHUNK = 1024 * 1024


class BackupError(ValueError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _derive_key(passphrase: str, salt: bytes, iterations: int) -> bytes:
    if len(passphrase) < 12:
        raise BackupError("Backup passphrase хамгийн багадаа 12 тэмдэгт байна.")
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations).derive(
        passphrase.encode("utf-8")
    )


def _encrypt_file(source: Path, target: Path, passphrase: str) -> None:
    salt = os.urandom(SALT_BYTES)
    nonce = os.urandom(NONCE_BYTES)
    iterations = KDF_ITERATIONS
    header = MAGIC + salt + nonce + struct.pack(">I", iterations)
    key = _derive_key(passphrase, salt, iterations)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(header)
    with source.open("rb") as src, target.open("wb") as dst:
        dst.write(header)
        for block in iter(lambda: src.read(CHUNK), b""):
            dst.write(encryptor.update(block))
        encryptor.finalize()
        dst.write(encryptor.tag)


def _decrypt_file(source: Path, target: Path, passphrase: str) -> None:
    size = source.stat().st_size
    if size < HEADER_LEN + TAG_BYTES:
        raise BackupError("Encrypted backup файл дутуу эсвэл эвдэрсэн байна.")
    with source.open("rb") as src:
        header = src.read(HEADER_LEN)
        if not header.startswith(MAGIC):
            raise BackupError("DUREM encrypted backup формат биш байна.")
        offset = len(MAGIC)
        salt = header[offset:offset + SALT_BYTES]
        offset += SALT_BYTES
        nonce = header[offset:offset + NONCE_BYTES]
        offset += NONCE_BYTES
        iterations = struct.unpack(">I", header[offset:offset + 4])[0]
        if iterations < 100_000 or iterations > 5_000_000:
            raise BackupError("Backup KDF параметр буруу байна.")
        key = _derive_key(passphrase, salt, iterations)
        src.seek(-TAG_BYTES, os.SEEK_END)
        tag = src.read(TAG_BYTES)
        encrypted_len = size - HEADER_LEN - TAG_BYTES
        src.seek(HEADER_LEN)
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(header)
        remaining = encrypted_len
        try:
            with target.open("wb") as dst:
                while remaining:
                    block = src.read(min(CHUNK, remaining))
                    if not block:
                        raise BackupError("Encrypted backup файл тасарсан байна.")
                    remaining -= len(block)
                    dst.write(decryptor.update(block))
                decryptor.finalize()
        except InvalidTag as exc:
            target.unlink(missing_ok=True)
            raise BackupError("Backup passphrase буруу эсвэл файл өөрчлөгдсөн байна.") from exc


def _make_plain_zip(target: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="durem-backup-stage-") as temp_dir:
        temp = Path(temp_dir)
        db_copy = temp / "durem.db"
        with connect() as source:
            dest = sqlite3.connect(db_copy)
            try:
                source.backup(dest)
            finally:
                dest.close()
        docs = temp / "documents"
        docs.mkdir(parents=True, exist_ok=True)
        if settings.documents_dir.exists():
            shutil.copytree(settings.documents_dir, docs, dirs_exist_ok=True)
        manifest = {
            "product": "DUREM",
            "format": 2,
            "version": settings.version,
            "company": get_setting("company_name", settings.company_name),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "database_sha256": _sha256(db_copy),
            "document_files": sum(1 for path in docs.rglob("*") if path.is_file()),
        }
        (temp / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for path in temp.rglob("*"):
                if path.is_file():
                    zf.write(path, path.relative_to(temp))


def _prune_backups(keep: int = 20) -> None:
    candidates = sorted(
        [*settings.backups_dir.glob("durem-backup-*.zip"), *settings.backups_dir.glob("durem-backup-*.durem")],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in candidates[keep:]:
        path.unlink(missing_ok=True)


def create_backup(passphrase: str = "") -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="durem-backup-") as temp_dir:
        plain = Path(temp_dir) / "backup.zip"
        _make_plain_zip(plain)
        if passphrase:
            target = settings.backups_dir / f"durem-backup-{stamp}.durem"
            _encrypt_file(plain, target, passphrase)
        else:
            target = settings.backups_dir / f"durem-backup-{stamp}.zip"
            shutil.copy2(plain, target)
    _prune_backups()
    return target


def _validate_zip_members(zf: zipfile.ZipFile) -> None:
    infos = zf.infolist()
    if len(infos) > 20_000:
        raise BackupError("Backup хэт олон файлтай байна.")
    total = 0
    max_total = settings.restore_max_mb * 1024 * 1024
    for info in infos:
        # ZIP entry names are POSIX-like by spec, but normalize backslashes so
        # archives created on or manipulated by Windows cannot bypass traversal checks.
        name = info.filename.replace("\\", "/")
        pure = PurePosixPath(name)
        unix_mode = (info.external_attr >> 16) & 0xFFFF
        drive_like = len(name) >= 2 and name[0].isalpha() and name[1] == ":"
        has_controls = any(ord(ch) < 32 for ch in name)
        if (
            not name
            or pure.is_absolute()
            or drive_like
            or ".." in pure.parts
            or has_controls
            or stat.S_ISLNK(unix_mode)
        ):
            raise BackupError("Backup дотор аюултай path/link илэрлээ.")
        total += max(0, info.file_size)
        if total > max_total:
            raise BackupError(f"Backup задлагдсан хэмжээ {settings.restore_max_mb}MB-аас их байна.")


def _validate_restore_stage(stage: Path) -> dict:
    manifest_path = stage / "manifest.json"
    db_path = stage / "durem.db"
    if not manifest_path.exists() or not db_path.exists():
        raise BackupError("DUREM backup-ийн manifest/database олдсонгүй.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BackupError("Backup manifest эвдэрсэн байна.") from exc
    if manifest.get("product") != "DUREM":
        raise BackupError("Энэ файл DUREM backup биш байна.")
    expected = manifest.get("database_sha256")
    if expected and not hmac.compare_digest(_sha256(db_path), str(expected)):
        raise BackupError("Backup database checksum таарахгүй байна.")
    source = sqlite3.connect(db_path)
    try:
        integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise BackupError(f"Backup database integrity алдаатай: {integrity}")
        tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        required = {"users", "roles", "documents", "rules", "settings"}
        if not required.issubset(tables):
            raise BackupError("Backup database DUREM schema бүрэн биш байна.")
    finally:
        source.close()
    return manifest


def restore_backup(source_file: Path, passphrase: str = "") -> dict:
    """Validate and restore a DUREM backup. Existing data is rolled back if restore fails."""
    if source_file.stat().st_size > settings.restore_max_mb * 1024 * 1024:
        raise BackupError(f"Backup файл {settings.restore_max_mb}MB-аас их байна.")

    with tempfile.TemporaryDirectory(prefix="durem-restore-", dir=settings.data_dir) as temp_dir:
        temp = Path(temp_dir)
        plain_zip = temp / "restore.zip"
        with source_file.open("rb") as handle:
            magic = handle.read(len(MAGIC))
        if magic == MAGIC:
            if not passphrase:
                raise BackupError("Encrypted backup-ийн passphrase шаардлагатай.")
            _decrypt_file(source_file, plain_zip, passphrase)
        else:
            shutil.copy2(source_file, plain_zip)

        stage = temp / "stage"
        stage.mkdir()
        try:
            with zipfile.ZipFile(plain_zip) as zf:
                _validate_zip_members(zf)
                zf.extractall(stage)
        except zipfile.BadZipFile as exc:
            raise BackupError("Backup ZIP эвдэрсэн байна.") from exc

        manifest = _validate_restore_stage(stage)
        incoming_db = stage / "durem.db"
        incoming_docs = stage / "documents"

        rollback_db = temp / "rollback.db"
        rollback_docs = temp / "rollback-documents"
        with connect() as live:
            dest = sqlite3.connect(rollback_db)
            try:
                live.backup(dest)
            finally:
                dest.close()
        if settings.documents_dir.exists():
            shutil.copytree(settings.documents_dir, rollback_docs, dirs_exist_ok=True)

        try:
            src = sqlite3.connect(incoming_db)
            try:
                with connect() as live:
                    src.backup(live)
            finally:
                src.close()

            new_docs = temp / "new-documents"
            new_docs.mkdir()
            if incoming_docs.exists():
                shutil.copytree(incoming_docs, new_docs, dirs_exist_ok=True)
            old_docs = temp / "old-documents"
            if settings.documents_dir.exists():
                settings.documents_dir.rename(old_docs)
            new_docs.rename(settings.documents_dir)
            shutil.rmtree(old_docs, ignore_errors=True)

            # Apply migrations and force all restored sessions to re-authenticate.
            init_db()
            with connect() as conn:
                conn.execute("DELETE FROM sessions")
                conn.execute("DELETE FROM api_tokens")
        except Exception:
            # Restore database and document directory from the rollback snapshot.
            src = sqlite3.connect(rollback_db)
            try:
                with connect() as live:
                    src.backup(live)
            finally:
                src.close()
            if settings.documents_dir.exists():
                shutil.rmtree(settings.documents_dir, ignore_errors=True)
            settings.documents_dir.mkdir(parents=True, exist_ok=True)
            if rollback_docs.exists():
                shutil.copytree(rollback_docs, settings.documents_dir, dirs_exist_ok=True)
            init_db()
            raise

    return {
        "ok": True,
        "company": str(manifest.get("company", "")),
        "backup_version": str(manifest.get("version", "")),
        "created_at": str(manifest.get("created_at", "")),
        "logout_required": True,
    }
