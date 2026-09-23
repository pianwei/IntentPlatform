"""Canonical HTTP origins safe for exact browser comparison and CSP source lists."""
import ipaddress
import re
from urllib.parse import urlsplit


def exact_origin(value):
    if not isinstance(value, str) or not value or any(ord(c) <= 32 or ord(c) == 127 for c in value) or any(c in value for c in ";*'\"\\"):
        raise ValueError("Origin contains unsupported characters")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        host = parsed.hostname
        if parsed.scheme not in {"http", "https"} or not host or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError("An exact HTTP(S) origin is required")
        if ":" in host:
            host = "[" + ipaddress.IPv6Address(host).compressed + "]"
        else:
            host = host.encode("idna").decode("ascii").lower()
            if len(host) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")):
                raise ValueError("Invalid origin hostname")
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("Invalid origin port")
        suffix = "" if port is None or port == (443 if parsed.scheme == "https" else 80) else ":" + str(port)
        canonical = parsed.scheme + "://" + host + suffix
        if canonical != value:
            raise ValueError("Origin must use canonical scheme, hostname and port")
        return canonical
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Use an exact canonical HTTP(S) origin") from exc
