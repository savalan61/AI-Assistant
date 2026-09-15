"""Outbound-endpoint validation for broker-configured base URLs.

A broker's super_admin chooses the ``base_url`` the assistant will POST to, and
the server then makes that outbound request with the broker's API key. Left
unvalidated, that is a server-side request forgery primitive: an endpoint could
point at loopback, private RFC1918 ranges, link-local addresses, or the cloud
metadata service (169.254.169.254).

This module is the single validation policy for such an endpoint, and it is
provider-agnostic — it checks *where* we would connect, never which vendor is
behind it. It is applied at two points:

* on write (the broker LLM configuration API), where a hostname is also
  resolved and every resolved address is checked, so a hostname that points at
  an internal address is rejected when it is configured;
* before an outbound request (the broker provider resolver), where the same
  syntax and address checks run cheaply without a DNS lookup on the hot path.

Residual limitation, stated explicitly: a hostname that resolves to a public
address when configured and is later re-pointed at a private one (DNS
rebinding) is only caught by the address checks, not by pinning the resolved
address for the request lifetime. Pinning is deliberately not implemented here
because it would require replacing the HTTP client's connection handling.
"""
import ipaddress
import socket
from urllib.parse import urlsplit

from app.core.config import settings


class UrlSecurityError(ValueError):
    """A configured outbound endpoint was rejected by the security policy.

    The message names the reason (scheme, or the kind of blocked address) but
    never contains credentials: callers translate it into their own boundary
    error for clients.
    """


# Only the schemes the OpenAI-compatible adapter can actually speak.
_ALLOWED_SCHEMES = ("http", "https")
# HTTPS is mandatory everywhere except local development, where a plain-HTTP
# self-hosted runtime is a legitimate convenience.
_INSECURE_SCHEME_ALLOWED_ENV = "development"
# Names that never resolve to a routable public address. Rejected up front so
# the intent is explicit and the check does not depend on the resolver.
_BLOCKED_HOSTNAMES = frozenset(
    {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback", "broadcasthost"}
)


def _is_development() -> bool:
    return settings.APP_ENV == _INSECURE_SCHEME_ALLOWED_ENV


def _blocked_reason(value: str) -> str | None:
    """Return why an IP literal is not a permitted destination, else ``None``.

    ``None`` also means "not an IP literal" — a hostname is resolved instead.
    IPv4-mapped IPv6 forms (``::ffff:127.0.0.1``) are unwrapped and re-checked,
    because they would otherwise reach a loopback address while looking like a
    v6 literal.
    """
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        return _blocked_reason(str(mapped))
    if address.is_loopback:
        return "loopback"
    if address.is_link_local:
        return "link-local"
    if address.is_multicast:
        return "multicast"
    if address.is_reserved:
        return "reserved"
    if address.is_unspecified:
        return "unspecified"
    if address.is_private:
        return "private"
    # Final catch-all: only publicly routable addresses are permitted. This
    # covers ranges the specific flags above miss, notably carrier-grade NAT
    # (100.64.0.0/10) and other non-global blocks.
    if not address.is_global:
        return "non-public"
    return None


def is_ip_literal(host: str) -> bool:
    """True when ``host`` is an IP address rather than a hostname."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def resolve_host_addresses(host: str) -> tuple[str, ...]:
    """Every address ``host`` currently resolves to, or raise UrlSecurityError.

    A hostname that cannot be resolved is rejected rather than allowed: the
    policy fails closed, and an endpoint nobody can resolve is unusable anyway.
    """
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError) as exc:
        raise UrlSecurityError("base_url host could not be resolved") from exc
    return tuple(sorted({str(info[4][0]) for info in infos}))


def validate_llm_base_url(base_url: str, *, resolve_host: bool = True) -> str:
    """Validate an outbound LLM endpoint; return the stripped URL unchanged.

    Raises UrlSecurityError for a non-http(s) scheme, a missing or blocked
    host, embedded credentials, an unusable port, a plaintext scheme outside
    development, or (when ``resolve_host``) any resolve failure or any address
    that resolves into a blocked range.
    """
    candidate = base_url.strip()
    parts = urlsplit(candidate)
    if parts.scheme not in _ALLOWED_SCHEMES or not parts.netloc:
        raise UrlSecurityError("base_url must be an absolute http(s) URL")
    if parts.scheme != "https" and not _is_development():
        raise UrlSecurityError("base_url must use https outside development")
    # Credentials in a URL would be silently used by the HTTP client and are a
    # configuration smell; reject instead of forwarding them.
    if parts.username is not None or parts.password is not None:
        raise UrlSecurityError("base_url must not embed credentials")
    try:
        port = parts.port
    except ValueError as exc:
        raise UrlSecurityError("base_url has an invalid port") from exc
    if port is not None and not 1 <= port <= 65535:
        raise UrlSecurityError("base_url has an invalid port")

    host = parts.hostname
    if not host:
        raise UrlSecurityError("base_url must include a host")
    # Case and a trailing dot are both ignored by DNS, so normalize before the
    # comparison to keep the blocklist effective.
    normalized = host.lower().rstrip(".")
    if normalized in _BLOCKED_HOSTNAMES:
        raise UrlSecurityError("base_url host is not permitted (local hostname)")

    if is_ip_literal(normalized):
        reason = _blocked_reason(normalized)
        if reason is not None:
            raise UrlSecurityError(f"base_url host is not permitted ({reason} address)")
        return candidate

    if resolve_host:
        # Every resolved address must be permitted: a hostname resolving to any
        # internal address would otherwise be a way in.
        for address in resolve_host_addresses(normalized):
            reason = _blocked_reason(address)
            if reason is not None:
                raise UrlSecurityError(f"base_url host is not permitted ({reason} address)")
    return candidate
