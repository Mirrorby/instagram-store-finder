"""Bounded public-page requests; validate every redirect and distinguish access denial."""
from __future__ import annotations
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse
import requests


@dataclass
class Page:
    url: str
    status: int | None = None
    text: str = ""
    error: str = ""
    content_type: str = ""


def public_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return False
        if parsed.port and parsed.port not in {80, 443}:
            return False
        host = parsed.hostname.lower()
        if host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
            return False
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            addresses = [ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)]
        return bool(addresses) and all(ip.is_global for ip in addresses)
    except socket.gaierror:
        raise
    except ValueError:
        return False


class Fetcher:
    def __init__(self, timeout: int = 8):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"})
        self.timeout = timeout

    def get(self, url: str, max_bytes: int = 2_000_000) -> Page:
        current = url
        for _ in range(6):
            try:
                if not public_url(current):
                    return Page(current, error="unsafe_url")
                with self.session.get(current, timeout=self.timeout, allow_redirects=False, stream=True) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        target = response.headers.get("Location")
                        if not target:
                            return Page(current, response.status_code, error="invalid_redirect")
                        current = urljoin(current, target)
                        continue
                    data = bytearray()
                    for chunk in response.iter_content(16384):
                        data.extend(chunk)
                        if len(data) >= max_bytes:
                            break
                    encoding = response.encoding or "utf-8"
                    return Page(current, response.status_code, bytes(data[:max_bytes]).decode(encoding, errors="replace"),
                                content_type=response.headers.get("Content-Type", ""))
            except socket.gaierror:
                return Page(current, error="dns_error")
            except requests.exceptions.SSLError:
                return Page(current, error="tls_error")
            except requests.exceptions.Timeout:
                return Page(current, error="timeout")
            except requests.exceptions.ConnectionError:
                return Page(current, error="connection_error")
            except requests.exceptions.RequestException:
                return Page(current, error="request_error")
        return Page(current, error="redirect_loop")
