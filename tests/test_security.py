from pathlib import Path
import hashlib
import io
import os
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.auth import password_needs_rehash, password_policy_error, verify_password
from app.backup import BackupError, _decrypt_file, _encrypt_file
from app.documents import validate_upload_bytes
from app.main import app


def test_password_policy_rejects_weak_and_accepts_strong():
    assert password_policy_error("short")
    assert password_policy_error("alllowercase123")
    assert password_policy_error("VeryStrong123!") is None


def test_pdf_active_action_rejected():
    with pytest.raises(ValueError):
        validate_upload_bytes(b"%PDF-1.7\n1 0 obj << /OpenAction 2 0 R /JavaScript (x) >>", "policy.pdf")


def test_office_macro_or_embedded_object_rejected():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<document/>")
        zf.writestr("word/vbaProject.bin", b"macro")
    with pytest.raises(ValueError):
        validate_upload_bytes(buffer.getvalue(), "policy.docx")


def test_encrypted_backup_roundtrip_and_wrong_password(tmp_path: Path):
    source = tmp_path / "plain.zip"
    encrypted = tmp_path / "backup.durem"
    restored = tmp_path / "restored.zip"
    source.write_bytes(b"DUREM encrypted backup test" * 1000)
    _encrypt_file(source, encrypted, "Correct-Horse-123!")
    _decrypt_file(encrypted, restored, "Correct-Horse-123!")
    assert restored.read_bytes() == source.read_bytes()
    with pytest.raises(BackupError):
        _decrypt_file(encrypted, tmp_path / "bad.zip", "Wrong-Password-123!")


def test_security_headers_and_docs_disabled():
    client = TestClient(app)
    response = client.get("/login")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["cross-origin-opener-policy"] == "same-origin"
    assert client.get("/docs").status_code == 404


def test_legacy_password_hashes_upgrade_path():
    password = "Legacy-Strong-123!"
    salt = os.urandom(16)
    scrypt_digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    legacy_scrypt = f"scrypt${2**14}$8$1${salt.hex()}${scrypt_digest.hex()}"
    assert verify_password(password, legacy_scrypt)
    assert password_needs_rehash(legacy_scrypt)

    salt2 = os.urandom(16)
    digest2 = hashlib.pbkdf2_hmac("sha256", password.encode(), salt2, 260_000)
    legacy_pbkdf2 = f"pbkdf2_sha256$260000${salt2.hex()}${digest2.hex()}"
    assert verify_password(password, legacy_pbkdf2)
    assert password_needs_rehash(legacy_pbkdf2)


def test_backup_zip_rejects_symlink_and_windows_traversal(tmp_path):
    import zipfile
    from app.backup import BackupError, _validate_zip_members

    symlink_zip = tmp_path / "symlink.zip"
    with zipfile.ZipFile(symlink_zip, "w") as zf:
        info = zipfile.ZipInfo("documents/link")
        info.create_system = 3
        info.external_attr = (0o120777 << 16)
        zf.writestr(info, "../../outside")
    with zipfile.ZipFile(symlink_zip) as zf:
        try:
            _validate_zip_members(zf)
        except BackupError:
            pass
        else:
            raise AssertionError("ZIP symlink should be rejected")

    traversal_zip = tmp_path / "traversal.zip"
    with zipfile.ZipFile(traversal_zip, "w") as zf:
        zf.writestr("..\\outside.txt", "x")
    with zipfile.ZipFile(traversal_zip) as zf:
        try:
            _validate_zip_members(zf)
        except BackupError:
            pass
        else:
            raise AssertionError("Windows-style ZIP traversal should be rejected")


def test_local_ai_boundary_and_document_lifecycle_access():
    from app.config import settings
    from app.main import _document_is_available_to_user

    assert settings.llm_endpoint_is_local
    employee = {"department": "Sales", "is_admin": False}
    admin = {"department": "", "is_admin": True}
    active = {
        "status": "active", "effective_from": "", "effective_to": "",
        "visibility": "all", "department": "",
    }
    archived = dict(active, status="archived")
    scoped = dict(active, visibility="department", department="Finance")
    assert _document_is_available_to_user(active, employee)
    assert not _document_is_available_to_user(archived, employee)
    assert not _document_is_available_to_user(scoped, employee)
    assert _document_is_available_to_user(archived, admin)
