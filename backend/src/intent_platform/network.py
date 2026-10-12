"""Pinned JSON transport; only the model path has a narrow development public exception."""
import ipaddress
import socket
import zlib
from itertools import chain
from urllib.parse import urlsplit
import httpx
from .config import settings

MAX_JSON_RESPONSE_BYTES = 5_000_000
DEEPSEEK_CHAT_COMPLETIONS_URL = "https://api.deepseek.com/chat/completions"
# Some development networks resolve public hosts through an RFC 2544
# benchmarking-range TLS gateway. This range is accepted only for the exact
# DeepSeek ModelPort exception; the original hostname still drives SNI and TLS
# certificate validation on the DNS-pinned connection.
DEEPSEEK_DEV_GATEWAY_RANGE = ipaddress.ip_network("198.18.0.0/15")


def _public_deepseek_model_url(url):
    cfg = settings()
    return (cfg.env == "development" and cfg.allow_public_deepseek_model_test
            and url == DEEPSEEK_CHAT_COMPLETIONS_URL)


def checked_url(url, *, model_endpoint=False):
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Endpoint must be an approved HTTP(S) URL without embedded credentials")
    allowed = {x.strip().lower() for x in settings().allowed_hosts.split(",") if x.strip()}
    public_model = model_endpoint and _public_deepseek_model_url(url)
    if parsed.hostname.lower() not in allowed and not public_model:
        raise ValueError("Endpoint host is not allowlisted")
    try:
        addresses = {x[4][0] for x in socket.getaddrinfo(
            parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM)}
    except OSError:
        raise ValueError("Endpoint DNS resolution failed") from None
    for raw in addresses:
        address = ipaddress.ip_address(raw)
        if public_model:
            # All other non-global addresses, including ordinary private and
            # loopback ranges, remain forbidden even with the development flag.
            if not address.is_global and address not in DEEPSEEK_DEV_GATEWAY_RANGE:
                raise ValueError("Public model endpoint must resolve to public or approved gateway addresses")
            continue
        if address.is_link_local or address.is_multicast or address.is_unspecified:
            raise ValueError("Unsafe endpoint address")
        if settings().env == "production" and (address.is_loopback or not address.is_private):
            raise ValueError("Production endpoints must resolve to private bank-network addresses")
        if settings().env != "production" and not (address.is_private or address.is_loopback):
            raise ValueError("Only private/local endpoint addresses are allowed")
    if not addresses:
        raise ValueError("Endpoint has no approved address")
    return parsed, sorted(addresses)[0]


def request_json(method, url, *, payload=None, headers=None, timeout=10):
    return _request_json(method, url, params=payload if method == "GET" else None,
                         json_body=payload if method != "GET" else None, headers=headers,
                         timeout=timeout, model_endpoint=False)


def request_model_json(url, *, payload, headers=None, timeout=10):
    """ModelPort transport; no business connector receives the public exception."""
    return _request_json("POST", url, params=None, json_body=payload, headers=headers,
                         timeout=timeout, model_endpoint=True)


def request_json_parts(method, url, *, params=None, json_body=None, headers=None, timeout=10, allow_empty=False):
    """HTTP-v2 transport keeps URL parameters independent from a JSON body."""
    return _request_json(method, url, params=params, json_body=json_body, headers=headers,
                         timeout=timeout, model_endpoint=False, allow_empty=allow_empty)


def _request_json(method, url, *, params, json_body, headers, timeout, model_endpoint, allow_empty=False):
    guard_transport_headers(headers)
    parsed, address = checked_url(url, model_endpoint=model_endpoint)
    pinned = httpx.URL(url).copy_with(host=address)
    # The pinned socket address avoids DNS rebinding between validation and connect.
    with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
        with client.stream(method, pinned, params=params, json=json_body,
                           headers={"Host": parsed.netloc, "Accept-Encoding": "identity", **(headers or {})},
                           extensions={"sni_hostname": parsed.hostname}) as response:
            if 300 <= response.status_code < 400:
                raise ValueError("Endpoint redirects are not permitted")
            body = _read_bounded(response)
            if response.is_error:
                # HTTP-v2's explicitly reviewed business failure codes inspect this
                # bounded response. Never attach an unbounded stream to the exception.
                bounded = httpx.Response(response.status_code, content=body, request=response.request)
                bounded.raise_for_status()
            if allow_empty and response.status_code == 204 and not body:
                return {}
            return httpx.Response(response.status_code, content=body, request=response.request).json()


def _read_bounded(response):
    """Read at most 5 MB of decoded JSON, including uncooperative gzip/deflate peers.

    iter_bytes() is insufficient here: a decoder can inflate a tiny wire chunk
    into an arbitrarily large object before yielding the first bounded chunk.
    """
    encoding = response.headers.get("Content-Encoding", "").strip().lower()
    if encoding not in {"", "identity", "gzip", "deflate"}:
        raise ValueError("Unsupported connector response encoding")
    # Bound wire bytes as well as decoded bytes. The extra allowance covers
    # normal gzip/deflate framing overhead near the decoded 5 MB ceiling.
    def raw_chunks():
        wire_bytes = 0
        for chunk in response.iter_raw(chunk_size=64 * 1024):
            wire_bytes += len(chunk)
            if wire_bytes > MAX_JSON_RESPONSE_BYTES + 64 * 1024:
                raise ValueError("Connector response exceeds permitted size")
            yield chunk

    body = bytearray()
    if encoding in {"", "identity"}:
        for chunk in raw_chunks():
            if len(chunk) > MAX_JSON_RESPONSE_BYTES - len(body):
                raise ValueError("Connector response exceeds permitted size")
            body.extend(chunk)
        return bytes(body)

    chunks = iter(raw_chunks())
    if encoding == "deflate":
        prefix = bytearray()
        while len(prefix) < 2:
            piece = next(chunks, None)
            if piece is None:
                raise ValueError("Incomplete connector response encoding")
            prefix.extend(piece)
        # RFC 1950 has a verifiable header; some peers send raw RFC 1951
        # deflate instead, which HTTPX previously accepted as well.
        wrapped = prefix[0] & 15 == 8 and (prefix[0] * 256 + prefix[1]) % 31 == 0
        decoder = zlib.decompressobj(zlib.MAX_WBITS if wrapped else -zlib.MAX_WBITS)
        chunks = chain((bytes(prefix),), chunks)
    else:
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    for chunk in chunks:
        pending = chunk
        while pending:
            try:
                decoded = decoder.decompress(pending, MAX_JSON_RESPONSE_BYTES - len(body) + 1)
            except zlib.error as exc:
                raise ValueError("Invalid connector response encoding") from exc
            if len(decoded) > MAX_JSON_RESPONSE_BYTES - len(body):
                raise ValueError("Connector response exceeds permitted size")
            body.extend(decoded)
            pending = decoder.unconsumed_tail
            if decoder.eof:
                pending = decoder.unused_data or pending
                if pending:
                    if encoding != "gzip":
                        raise ValueError("Invalid connector response encoding")
                    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    if not decoder.eof:
        raise ValueError("Incomplete connector response encoding")
    return bytes(body)


def guard_transport_headers(headers):
    for key in (headers or {}):
        if key.lower() in {"host", "content-length", "transfer-encoding", "connection", "te", "trailer", "upgrade", "expect", "forwarded", "via", "accept-encoding"} or key.lower().startswith(("x-forwarded-", "proxy-", "sec-")):
            raise ValueError("Caller headers cannot override transport routing or framing")
