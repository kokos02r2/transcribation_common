import ipaddress
import socket
from urllib.parse import urlsplit


def _is_public_ip(ip_value: str) -> bool:
    address = ipaddress.ip_address(ip_value)
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _resolve_host_ips(hostname: str) -> set[str]:
    try:
        records = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("Webhook host cannot be resolved") from exc

    ips = {record[4][0] for record in records}
    if not ips:
        raise ValueError("Webhook host cannot be resolved")
    return ips


def validate_webhook_url(
    webhook_url: str,
    *,
    allow_http: bool = False,
    allow_private_hosts: bool = False,
) -> str:
    if not webhook_url:
        raise ValueError("Webhook URL is required")

    parsed = urlsplit(webhook_url)
    allowed_schemes = {"https"} if not allow_http else {"http", "https"}
    if parsed.scheme not in allowed_schemes:
        if allow_http:
            raise ValueError("Webhook URL must start with http:// or https://")
        raise ValueError("Webhook URL must start with https://")

    if not parsed.hostname:
        raise ValueError("Webhook URL must include a valid hostname")

    if parsed.username or parsed.password:
        raise ValueError("Credentials in webhook URL are not allowed")

    hostname = parsed.hostname.strip().lower()
    if hostname in {"localhost", "localhost.localdomain"}:
        raise ValueError("Local webhook hosts are not allowed")

    if allow_private_hosts:
        return webhook_url

    try:
        ip_literal = ipaddress.ip_address(hostname)
    except ValueError:
        ip_literal = None

    if ip_literal is not None:
        if not _is_public_ip(str(ip_literal)):
            raise ValueError("Private webhook hosts are not allowed")
        return webhook_url

    resolved_ips = _resolve_host_ips(hostname)
    if any(not _is_public_ip(ip_value) for ip_value in resolved_ips):
        raise ValueError("Webhook host resolves to private/local address")

    return webhook_url
