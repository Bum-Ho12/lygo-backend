"""Guard for firm-supplied storage endpoints (SSRF).

A firm owner types the endpoint of "their" bucket, and Lygo's servers then connect to it. Without a check, that endpoint
could be Lygo's own network: cloud metadata (169.254.169.254), the database, internal admin ports. So outside local
development an endpoint must be https, carry no credentials in the URL, and resolve only to public addresses.

This is checked when a destination is saved and again every time it is used (DNS can change after saving). It narrows
the risk but cannot pin the address the S3 client finally connects to, so production should also block egress from
the API to private ranges at the network level.
"""
import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

from app.core.config import settings


class EndpointNotAllowed(ValueError):
    """The reason is safe to show to the firm owner."""


def _addresses(host: str, port: int) -> list[str]:
    return list({str(info[4][0]) for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})


async def check_endpoint(url: str | None) -> None:
    """Raise EndpointNotAllowed unless the endpoint is acceptable. None/empty means the provider's default (AWS)."""
    if not url:
        return
    parts = urlsplit(url.strip())
    allow_private = settings.is_local and not settings.storage_block_private_endpoints  # local dev talks to MinIO/moto on localhost
    if parts.scheme not in ({"https", "http"} if allow_private else {"https"}):
        raise EndpointNotAllowed("Use an https:// endpoint.")
    if parts.username or parts.password:
        raise EndpointNotAllowed("Don't put credentials in the endpoint; use the access key fields.")
    if not parts.hostname:
        raise EndpointNotAllowed("The endpoint needs a host name, e.g. https://s3.example.com.")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise EndpointNotAllowed("Give only the endpoint address (no path); the bucket goes in its own field.")
    if allow_private:
        return
    try:
        port = parts.port or 443
    except ValueError as exc:
        raise EndpointNotAllowed("The endpoint's port is not valid.") from exc
    try:
        addresses = await asyncio.to_thread(_addresses, parts.hostname, port)
    except (socket.gaierror, UnicodeError) as exc:
        raise EndpointNotAllowed("That endpoint's host name could not be found.") from exc
    for raw in addresses:
        ip = ipaddress.ip_address(raw.split("%", 1)[0])
        if not ip.is_global or ip.is_multicast:
            raise EndpointNotAllowed("That endpoint points to a private or internal network address, which isn't allowed.")
