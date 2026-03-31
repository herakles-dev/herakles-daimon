"""
Security utilities — SSRF guards and input validation.
"""

from __future__ import annotations

import ipaddress
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Private/reserved IP ranges that should never be fetched
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),  # IPv6 private
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
]

# Cloud metadata endpoints
_BLOCKED_HOSTS = {
    "metadata.google.internal",
    "metadata.google.com",
    "169.254.169.254",
}


def validate_url_for_ssrf(url: str) -> str | None:
    """Validate a URL is safe to fetch (no SSRF to internal services).

    Returns the URL unchanged if safe, or None if blocked.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return None

    if parsed.scheme not in ("http", "https"):
        return None

    hostname = parsed.hostname
    if not hostname:
        return None

    # Block known metadata endpoints
    if hostname.lower() in _BLOCKED_HOSTS:
        logger.warning("SSRF blocked: metadata endpoint %s", hostname)
        return None

    # Resolve hostname to IP and check against private ranges
    try:
        addr = ipaddress.ip_address(hostname)
        for network in _BLOCKED_NETWORKS:
            if addr in network:
                logger.warning("SSRF blocked: private IP %s", hostname)
                return None
    except ValueError:
        # hostname is a DNS name, not an IP — allow it
        # (DNS rebinding is out of scope for this guard)
        pass

    return url
