"""Focused tests for the outbound-endpoint (SSRF) validation policy.

Require none of: network, DNS, database, MT5, credentials. The resolver is
stubbed so every case is deterministic and offline.
"""
import socket

import pytest

from app.core import url_security
from app.core.config import settings as app_settings
from app.core.url_security import UrlSecurityError, is_ip_literal, validate_llm_base_url

PUBLIC_ADDRESS = "93.184.216.34"


@pytest.fixture(autouse=True)
def development_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # Base case: local development, where plain http is tolerated.
    monkeypatch.setattr(app_settings, "APP_ENV", "development", raising=True)


@pytest.fixture()
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every hostname resolves to one public address."""

    def fake_getaddrinfo(host: str, port: object, *args: object, **kwargs: object) -> list[tuple]:
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (PUBLIC_ADDRESS, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def stub_dns(monkeypatch: pytest.MonkeyPatch, *addresses: str) -> None:
    """Make every hostname resolve to exactly ``addresses``."""

    def fake_getaddrinfo(host: str, port: object, *args: object, **kwargs: object) -> list[tuple]:
        return [
            (socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))
            for address in addresses
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


# --- accepted endpoints --------------------------------------------------------------


def test_public_https_hostname_is_accepted(public_dns) -> None:
    assert validate_llm_base_url("https://llm.example.com/v1") == "https://llm.example.com/v1"


def test_value_is_stripped() -> None:
    assert validate_llm_base_url("  https://93.184.216.34/v1  ") == "https://93.184.216.34/v1"


def test_public_ip_literal_is_accepted_without_dns() -> None:
    # A literal address needs no lookup, so this passes with no resolver stub.
    assert validate_llm_base_url("https://93.184.216.34/v1") == "https://93.184.216.34/v1"


def test_http_is_accepted_in_development(public_dns) -> None:
    assert validate_llm_base_url("http://llm.example.com/v1") == "http://llm.example.com/v1"


def test_skip_resolution_leaves_hostname_unchecked(monkeypatch: pytest.MonkeyPatch) -> None:
    # The call-time (hot path) mode runs no DNS lookup at all.
    def exploding_getaddrinfo(*args: object, **kwargs: object) -> list[tuple]:
        raise AssertionError("resolve_host=False must not perform a DNS lookup")

    monkeypatch.setattr(socket, "getaddrinfo", exploding_getaddrinfo)

    assert validate_llm_base_url("https://llm.example.com/v1", resolve_host=False) == "https://llm.example.com/v1"


# --- scheme and shape ------------------------------------------------------------------


@pytest.mark.parametrize(
    "base_url",
    ["not-a-url", "/relative/path", "ftp://llm.example.com/v1", "file:///etc/passwd", "javascript:alert(1)", ""],
)
def test_non_http_endpoint_is_rejected(base_url: str, public_dns) -> None:
    with pytest.raises(UrlSecurityError):
        validate_llm_base_url(base_url)


def test_https_is_required_outside_development(monkeypatch: pytest.MonkeyPatch, public_dns) -> None:
    monkeypatch.setattr(app_settings, "APP_ENV", "production", raising=True)

    with pytest.raises(UrlSecurityError, match="https"):
        validate_llm_base_url("http://llm.example.com/v1")


def test_https_is_accepted_outside_development(monkeypatch: pytest.MonkeyPatch, public_dns) -> None:
    monkeypatch.setattr(app_settings, "APP_ENV", "production", raising=True)

    assert validate_llm_base_url("https://llm.example.com/v1") == "https://llm.example.com/v1"


def test_embedded_credentials_are_rejected(public_dns) -> None:
    with pytest.raises(UrlSecurityError, match="credentials"):
        validate_llm_base_url("https://user:secret@llm.example.com/v1")


def test_invalid_port_is_rejected(public_dns) -> None:
    with pytest.raises(UrlSecurityError, match="port"):
        validate_llm_base_url("https://llm.example.com:99999/v1")


# --- blocked destinations --------------------------------------------------------------


@pytest.mark.parametrize(
    "base_url",
    [
        "https://localhost/v1",
        "https://LOCALHOST./v1",  # case and trailing dot are DNS-insignificant
        "https://ip6-localhost/v1",
        "https://127.0.0.1/v1",
        "https://127.1.2.3/v1",
        "https://0.0.0.0/v1",
        "https://[::1]/v1",
        "https://169.254.169.254/latest/meta-data/",  # cloud metadata
        "https://10.0.0.5/v1",
        "https://192.168.1.10/v1",
        "https://172.16.0.9/v1",
        "https://[fd00::1]/v1",
        "https://[::ffff:127.0.0.1]/v1",  # IPv4-mapped loopback
        "https://100.64.0.1/v1",  # carrier-grade NAT
    ],
)
def test_local_and_private_destinations_are_rejected(base_url: str, public_dns) -> None:
    with pytest.raises(UrlSecurityError, match="not permitted"):
        validate_llm_base_url(base_url)


def test_hostname_resolving_to_a_private_address_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_dns(monkeypatch, "10.0.0.5")

    with pytest.raises(UrlSecurityError, match="private"):
        validate_llm_base_url("https://internal.attacker.example/v1")


def test_hostname_resolving_to_metadata_address_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_dns(monkeypatch, "169.254.169.254")

    with pytest.raises(UrlSecurityError, match="link-local"):
        validate_llm_base_url("https://metadata.attacker.example/v1")


def test_any_private_address_among_several_is_enough_to_reject(monkeypatch: pytest.MonkeyPatch) -> None:
    # A DNS answer mixing a public and a private address is still an entry point
    # to the private one, so it must be refused.
    stub_dns(monkeypatch, PUBLIC_ADDRESS, "192.168.0.7")

    with pytest.raises(UrlSecurityError, match="private"):
        validate_llm_base_url("https://mixed.attacker.example/v1")


def test_unresolvable_hostname_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_getaddrinfo(*args: object, **kwargs: object) -> list[tuple]:
        raise socket.gaierror("name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", failing_getaddrinfo)

    with pytest.raises(UrlSecurityError, match="could not be resolved"):
        validate_llm_base_url("https://no-such-host.example.invalid/v1")


# --- error hygiene and helpers ---------------------------------------------------------


def test_error_message_never_contains_the_embedded_secret(public_dns) -> None:
    secret = "super-secret-url-password"

    with pytest.raises(UrlSecurityError) as excinfo:
        validate_llm_base_url(f"https://user:{secret}@llm.example.com/v1")

    assert secret not in str(excinfo.value)


@pytest.mark.parametrize(
    ("host", "expected"),
    [("127.0.0.1", True), ("::1", True), ("93.184.216.34", True), ("llm.example.com", False), ("", False)],
)
def test_is_ip_literal(host: str, expected: bool) -> None:
    assert is_ip_literal(host) is expected


def test_module_exposes_no_logging_of_the_candidate() -> None:
    import inspect

    source = inspect.getsource(url_security)

    # The policy must never log; it only raises.
    assert "logger" not in source
    assert "logging" not in source
