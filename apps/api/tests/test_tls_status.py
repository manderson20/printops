from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.core.tls_status import read_certificate_status


def _write_self_signed_cert(path: Path, days_valid: int) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "print.example.org")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=days_valid))
        .sign(key, hashes.SHA256())
    )
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def test_read_certificate_status_missing_file_returns_none(tmp_path):
    assert read_certificate_status(tmp_path / "does-not-exist.crt") is None


def test_read_certificate_status_parses_real_cert(tmp_path):
    cert_path = tmp_path / "test.crt"
    _write_self_signed_cert(cert_path, days_valid=60)

    status = read_certificate_status(cert_path)
    assert status is not None
    assert "print.example.org" in status.issuer
    assert 58 <= status.days_remaining <= 60


def test_read_certificate_status_garbage_file_returns_none(tmp_path):
    cert_path = tmp_path / "garbage.crt"
    cert_path.write_bytes(b"not a real certificate")
    assert read_certificate_status(cert_path) is None
