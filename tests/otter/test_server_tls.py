"""Tests for ``chimera.otter.server`` TLS support (O-SERVER-3).

The HTTP server gains stdlib-only TLS via :class:`ssl.SSLContext`. These
tests cover three slices:

* **Constructor wiring.** Passing only one of ``tls_cert`` / ``tls_key``
  must raise :class:`ValueError`; passing neither leaves the server in
  cleartext mode (covered by the existing 26 server tests).
* **End-to-end HTTPS.** When both flags are set the listen socket is
  wrapped, ``GET /healthz`` over HTTPS returns ``{"status": "ok"}``, and
  the bearer-token auth path is preserved on top of TLS.
* **CLI plumbing.** ``add_arguments`` exposes ``--tls-cert`` /
  ``--tls-key``, and ``_dispatch_serve_http`` rejects a half-configured
  pair before binding.

We rely on :mod:`cryptography` to mint a self-signed certificate in
``tmp_path``. The whole module is gated behind ``pytest.importorskip``
so machines without ``cryptography`` simply skip these tests instead of
breaking the wave-1 baseline.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator

import pytest

# Skip the entire module when cryptography is unavailable. We could fall
# back to shelling out to ``openssl`` but that adds a hidden binary
# dependency and the test surface is identical either way.
cryptography = pytest.importorskip("cryptography")

from cryptography import x509  # noqa: E402  -- imported after gating
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

from chimera.otter import cli as otter_cli  # noqa: E402
from chimera.otter.server import OtterServer  # noqa: E402


# ---------------------------------------------------------------------------
# Self-signed cert generation (test-only)
# ---------------------------------------------------------------------------


def _make_self_signed_cert(tmp_path: Path) -> tuple[Path, Path]:
    """Mint a fresh self-signed cert + key for ``127.0.0.1`` under *tmp_path*.

    The cert binds CN=``localhost`` plus a SAN of ``DNS:localhost`` and
    ``IP:127.0.0.1`` so urllib accepts it after we install it as a
    trust anchor on the client context. We use a 2048-bit RSA key
    (faster than 4096 for unit tests; still meets modern minima).

    Returns:
        A ``(cert_path, key_path)`` pair in ``tmp_path``.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]
    )
    now = _dt.datetime.now(_dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(minutes=5))
        .not_valid_after(now + _dt.timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(__import__("ipaddress").ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "server.crt"
    key_path = tmp_path / "server.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def cert_pair(tmp_path: Path) -> tuple[Path, Path]:
    """Per-test self-signed cert + key under :func:`tmp_path`."""
    return _make_self_signed_cert(tmp_path)


@pytest.fixture()
def tls_server(cert_pair: tuple[Path, Path]) -> Iterator[OtterServer]:
    """A TLS-enabled :class:`OtterServer` on an OS-chosen port."""
    cert, key = cert_pair
    srv = OtterServer(
        agent_factory=None,
        host="127.0.0.1",
        port=0,
        tls_cert=cert,
        tls_key=key,
    )
    srv.start(blocking=False)
    try:
        yield srv
    finally:
        srv.shutdown()


@pytest.fixture()
def tls_auth_server(cert_pair: tuple[Path, Path]) -> Iterator[OtterServer]:
    """A TLS-enabled server that *also* enforces a bearer token."""
    cert, key = cert_pair
    srv = OtterServer(
        agent_factory=None,
        host="127.0.0.1",
        port=0,
        auth_token="tls-secret",
        tls_cert=cert,
        tls_key=key,
    )
    srv.start(blocking=False)
    try:
        yield srv
    finally:
        srv.shutdown()


# ---------------------------------------------------------------------------
# HTTPS helpers
# ---------------------------------------------------------------------------


def _https_get(
    srv: OtterServer,
    path: str,
    cert_path: Path,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> tuple[int, dict[str, Any]]:
    """Issue an HTTPS GET against *srv* trusting *cert_path*."""
    ctx = ssl.create_default_context(cafile=str(cert_path))
    # The cert SAN covers ``localhost`` so hostname verification works
    # without disabling the check.
    url = f"https://localhost:{srv.port}{path}"
    req = urllib.request.Request(url, method="GET")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"_raw": raw.decode("utf-8", "replace")}
    raw = resp.read()
    return resp.status, json.loads(raw) if raw else {}


def _https_post(
    srv: OtterServer,
    path: str,
    body: dict[str, Any],
    cert_path: Path,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> tuple[int, dict[str, Any]]:
    """Issue an HTTPS POST against *srv* trusting *cert_path*."""
    ctx = ssl.create_default_context(cafile=str(cert_path))
    url = f"https://localhost:{srv.port}{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"_raw": raw.decode("utf-8", "replace")}
    raw = resp.read()
    return resp.status, json.loads(raw) if raw else {}


# ---------------------------------------------------------------------------
# Constructor wiring
# ---------------------------------------------------------------------------


def test_tls_requires_both_cert_and_key(tmp_path: Path) -> None:
    cert, key = _make_self_signed_cert(tmp_path)
    with pytest.raises(ValueError):
        OtterServer(host="127.0.0.1", port=0, tls_cert=cert)
    with pytest.raises(ValueError):
        OtterServer(host="127.0.0.1", port=0, tls_key=key)


def test_no_tls_when_neither_flag_set() -> None:
    """The default cleartext path stays untouched (no socket wrap)."""
    srv = OtterServer(host="127.0.0.1", port=0)
    srv.start(blocking=False)
    try:
        # The httpd's socket should be a plain socket, not SSLSocket.
        assert not isinstance(srv._httpd.socket, ssl.SSLSocket)  # type: ignore[union-attr]
    finally:
        srv.shutdown()


# ---------------------------------------------------------------------------
# End-to-end HTTPS
# ---------------------------------------------------------------------------


def test_healthz_over_https(
    tls_server: OtterServer, cert_pair: tuple[Path, Path]
) -> None:
    cert, _key = cert_pair
    status, body = _https_get(tls_server, "/healthz", cert)
    assert status == 200
    assert body == {"status": "ok"}


def test_socket_is_ssl_wrapped(tls_server: OtterServer) -> None:
    """Listening socket is an ``SSLSocket`` once TLS is enabled."""
    assert isinstance(tls_server._httpd.socket, ssl.SSLSocket)  # type: ignore[union-attr]


def test_plain_http_to_tls_server_fails(tls_server: OtterServer) -> None:
    """Cleartext clients can't talk to a TLS-wrapped listener."""
    url = f"http://127.0.0.1:{tls_server.port}/healthz"
    with pytest.raises((urllib.error.URLError, ConnectionResetError)):
        urllib.request.urlopen(url, timeout=2.0)


# ---------------------------------------------------------------------------
# Bearer auth on top of TLS
# ---------------------------------------------------------------------------


def test_tls_plus_bearer_auth_accepts_valid_token(
    tls_auth_server: OtterServer, cert_pair: tuple[Path, Path]
) -> None:
    cert, _key = cert_pair
    status, body = _https_post(
        tls_auth_server,
        "/session",
        {},
        cert,
        headers={"Authorization": "Bearer tls-secret"},
    )
    assert status == 201
    assert "session_id" in body


def test_tls_plus_bearer_auth_rejects_missing_token(
    tls_auth_server: OtterServer, cert_pair: tuple[Path, Path]
) -> None:
    cert, _key = cert_pair
    status, body = _https_post(tls_auth_server, "/session", {}, cert)
    assert status == 401
    assert body == {"error": "unauthorized"}


def test_tls_healthz_skips_auth(
    tls_auth_server: OtterServer, cert_pair: tuple[Path, Path]
) -> None:
    """``/healthz`` answers even with TLS+auth enabled — same as cleartext."""
    cert, _key = cert_pair
    status, body = _https_get(tls_auth_server, "/healthz", cert)
    assert status == 200
    assert body["status"] == "ok"


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------


def test_add_arguments_exposes_tls_flags() -> None:
    parser = argparse.ArgumentParser()
    otter_cli.add_arguments(parser)
    ns = parser.parse_args(
        [
            "--tls-cert",
            "/tmp/cert.pem",
            "--tls-key",
            "/tmp/key.pem",
            "serve",
        ]
    )
    assert ns.tls_cert == "/tmp/cert.pem"
    assert ns.tls_key == "/tmp/key.pem"
    assert ns.subcommand == "serve"


def test_add_arguments_default_tls_flags_none() -> None:
    parser = argparse.ArgumentParser()
    otter_cli.add_arguments(parser)
    ns = parser.parse_args(["serve"])
    assert ns.tls_cert is None
    assert ns.tls_key is None


def test_dispatch_serve_http_rejects_half_tls(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Only one of --tls-cert / --tls-key set is a usage error (rc=2)."""
    cert, _key = _make_self_signed_cert(tmp_path)
    args = argparse.Namespace(
        cwd=str(tmp_path),
        model="ignored",
        max_steps=1,
        host="127.0.0.1",
        port=0,
        auth_token=None,
        tls_cert=str(cert),
        tls_key=None,
        no_lsp=True,
        no_rules=True,
        no_mcp=True,
        no_plugins=True,
    )
    rc = otter_cli._dispatch_serve_http(args)
    assert rc == 2
    captured = capsys.readouterr()
    assert "tls-cert" in captured.err and "tls-key" in captured.err


def test_dispatch_serve_http_passes_tls_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A complete TLS pair flows through to ``serve_http`` unchanged."""
    cert, key = _make_self_signed_cert(tmp_path)
    captured: dict[str, Any] = {}

    def _fake_serve_http(
        _factory: Any,
        *,
        host: str,
        port: int,
        auth_token: str | None,
        tls_cert: Any,
        tls_key: Any,
        pidfile_prefix: str,
    ) -> int:
        captured["host"] = host
        captured["port"] = port
        captured["auth_token"] = auth_token
        captured["tls_cert"] = tls_cert
        captured["tls_key"] = tls_key
        # Added in e5d4d725 (serve mgmt) and never reflected here, so this fake
        # raised TypeError on every call. The module is behind
        # `importorskip("cryptography")`, which CI does not install — so the
        # break never failed a build. Captured rather than swallowed with
        # **kwargs: a fake that silently absorbs new arguments stops testing the
        # call contract it exists to test.
        captured["pidfile_prefix"] = pidfile_prefix
        return 0

    monkeypatch.setattr(
        "chimera.otter.server.serve_http", _fake_serve_http
    )

    args = argparse.Namespace(
        cwd=str(tmp_path),
        model="ignored",
        max_steps=1,
        host="127.0.0.1",
        port=0,
        auth_token="tok",
        tls_cert=str(cert),
        tls_key=str(key),
        no_lsp=True,
        no_rules=True,
        no_mcp=True,
        no_plugins=True,
    )
    rc = otter_cli._dispatch_serve_http(args)
    assert rc == 0
    assert captured["tls_cert"] == str(cert)
    assert captured["tls_key"] == str(key)
    assert captured["pidfile_prefix"] == "otter"
    assert captured["auth_token"] == "tok"
