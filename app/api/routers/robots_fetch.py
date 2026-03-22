from dataclasses import dataclass
import gzip
import io
import ipaddress
import re
import socket
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import aiohttp


@dataclass
class _HttpResponseData:
    url: str
    status_code: int
    headers: Dict[str, Any]
    content: bytes
    text: str = ""


def _decode_http_text(content: bytes, headers: Optional[Dict[str, Any]] = None) -> str:
    content_type = str((headers or {}).get("Content-Type") or (headers or {}).get("content-type") or "")
    match = re.search(r"charset=([^\s;]+)", content_type, flags=re.IGNORECASE)
    encoding = match.group(1).strip("\"'") if match else "utf-8"
    try:
        return (content or b"").decode(encoding, errors="replace")
    except LookupError:
        return (content or b"").decode("utf-8", errors="replace")


async def _async_http_get(
    session: aiohttp.ClientSession,
    url: str,
    timeout: int = 20,
    allow_redirects: bool = True,
    read_text: bool = True,
    text_limit: Optional[int] = None,
    use_proxy: bool = False,
) -> _HttpResponseData:
    timeout_cfg = aiohttp.ClientTimeout(total=timeout)
    proxy_kwargs: dict = {}
    if use_proxy:
        from app.proxy import get_aiohttp_proxy
        _proxy = get_aiohttp_proxy()
        if _proxy:
            proxy_kwargs["proxy"] = _proxy
    async with session.get(url, timeout=timeout_cfg, allow_redirects=allow_redirects, **proxy_kwargs) as resp:
        content = await resp.read()
        headers = dict(resp.headers)
        text = ""
        if read_text:
            decoded = _decode_http_text(content, headers)
            text = decoded[:text_limit] if text_limit else decoded
        return _HttpResponseData(
            url=str(resp.url),
            status_code=resp.status,
            headers=headers,
            content=content,
            text=text,
        )


class _AsyncSessionShim:
    def __init__(self, headers: Optional[Dict[str, str]] = None, use_proxy: bool = False):
        self.headers = dict(headers or {})
        self._session: Optional[aiohttp.ClientSession] = None
        self.use_proxy = use_proxy

    async def open(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=self.headers)
        return self

    async def close(self):
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def get(
        self,
        url: str,
        timeout: int = 20,
        allow_redirects: bool = True,
        headers: Optional[Dict[str, str]] = None,
        read_text: bool = True,
        text_limit: Optional[int] = None,
    ) -> _HttpResponseData:
        request_headers = dict(self.headers)
        if headers:
            request_headers.update(headers)
        if self._session is not None and not self._session.closed:
            return await _async_http_get(
                self._session,
                url,
                timeout=timeout,
                allow_redirects=allow_redirects,
                read_text=read_text,
                text_limit=text_limit,
                use_proxy=self.use_proxy,
            )
        async with aiohttp.ClientSession(headers=request_headers) as session:
            return await _async_http_get(
                session,
                url,
                timeout=timeout,
                allow_redirects=allow_redirects,
                read_text=read_text,
                text_limit=text_limit,
                use_proxy=self.use_proxy,
            )


_REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}


def _response_header(headers: Optional[Dict[str, Any]], key: str) -> str:
    header_map = headers or {}
    return str(header_map.get(key) or header_map.get(key.lower()) or "")


def _root_site_url(url: str) -> str:
    parsed = urlparse(str(url or ""))
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return str(url or "").strip()


def _is_public_ip_address(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(str(value or "").strip())
    except ValueError:
        return False
    return bool(ip.is_global)


def _is_public_hostname(hostname: str) -> bool:
    host = str(hostname or "").strip().rstrip(".")
    if not host:
        return False
    lowered = host.lower()
    if lowered in {"localhost", "localhost.localdomain"} or lowered.endswith(".local"):
        return False
    if lowered in {"example.com", "example.net", "example.org"} or lowered.endswith(".example"):
        return True
    if _is_public_ip_address(host):
        return True
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    addresses = set()
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        addr = str(sockaddr[0] or "").strip()
        if not addr:
            continue
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        addresses.add(ip)
    return bool(addresses) and all(ip.is_global for ip in addresses)


def _get_public_target_error(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in ("http", "https"):
        return "Разрешены только HTTP/HTTPS URL."
    hostname = str(parsed.hostname or "").strip()
    if not hostname:
        return "URL должен содержать домен."
    if not _is_public_hostname(hostname):
        return "Приватные, loopback и локальные адреса недоступны для проверки."
    return ""


def _safe_fetch_with_redirects_sync(
    client,
    url: str,
    timeout: int = 20,
    headers: Optional[Dict[str, str]] = None,
    max_redirects: int = 5,
):
    current_url = str(url or "").strip()
    initial_url = current_url
    seen: set[str] = set()
    for _ in range(max_redirects + 1):
        if current_url != initial_url:
            target_error = _get_public_target_error(current_url)
            if target_error:
                raise ValueError(target_error)
        response = client.get(current_url, timeout=timeout, headers=headers, allow_redirects=False)
        response_url = str(getattr(response, "url", current_url) or current_url)
        if response_url != initial_url:
            response_error = _get_public_target_error(response_url)
            if response_error:
                raise ValueError(response_error)
        location = _response_header(getattr(response, "headers", {}), "Location").strip()
        if getattr(response, "status_code", None) in _REDIRECT_STATUS_CODES and location:
            next_url = urljoin(response_url, location)
            if next_url in seen:
                raise ValueError("Обнаружен цикл редиректов при запросе.")
            seen.add(next_url)
            current_url = next_url
            continue
        return response
    raise ValueError("Превышен лимит редиректов при запросе.")


async def _safe_fetch_with_redirects_async(
    client,
    url: str,
    timeout: int = 20,
    headers: Optional[Dict[str, str]] = None,
    max_redirects: int = 5,
    read_text: bool = True,
    text_limit: Optional[int] = None,
    use_proxy: bool = False,
    async_http_get_fn=None,
):
    current_url = str(url or "").strip()
    initial_url = current_url
    seen: set[str] = set()
    for _ in range(max_redirects + 1):
        if current_url != initial_url:
            target_error = _get_public_target_error(current_url)
            if target_error:
                raise ValueError(target_error)
        try:
            response = await client.get(
                current_url,
                timeout=timeout,
                headers=headers,
                allow_redirects=False,
                read_text=read_text,
                text_limit=text_limit,
            )
        except TypeError as exc:
            if "read_text" not in str(exc) and "text_limit" not in str(exc):
                raise
            async_get_kwargs = {
                "timeout": timeout,
                "allow_redirects": False,
                "read_text": read_text,
                "text_limit": text_limit,
            }
            if use_proxy:
                async_get_kwargs["use_proxy"] = True
            fetcher = async_http_get_fn or _async_http_get
            response = await fetcher(client, current_url, **async_get_kwargs)
        response_url = str(getattr(response, "url", current_url) or current_url)
        if response_url != initial_url:
            response_error = _get_public_target_error(response_url)
            if response_error:
                raise ValueError(response_error)
        location = _response_header(getattr(response, "headers", {}), "Location").strip()
        if getattr(response, "status_code", None) in _REDIRECT_STATUS_CODES and location:
            next_url = urljoin(response_url, location)
            if next_url in seen:
                raise ValueError("Обнаружен цикл редиректов при запросе.")
            seen.add(next_url)
            current_url = next_url
            continue
        return response
    raise ValueError("Превышен лимит редиректов при запросе.")


def _looks_like_sitemap_bytes(payload: bytes) -> bool:
    head = (payload or b"")[:20000].lstrip(b"\xef\xbb\xbf \n\r\t").lower()
    return head.startswith(b"<?xml") or b"<urlset" in head or b"<sitemapindex" in head


def _response_looks_gzipped_sitemap(url: str, headers: Optional[Dict[str, Any]], payload: bytes) -> bool:
    header_map = headers or {}
    content_type = str(header_map.get("Content-Type") or header_map.get("content-type") or "").lower()
    content_encoding = str(header_map.get("Content-Encoding") or header_map.get("content-encoding") or "").lower()
    path = (urlparse(str(url or "")).path or "").lower()
    return (
        path.endswith(".gz")
        or "gzip" in content_type
        or "x-gzip" in content_type
        or "gzip" in content_encoding
        or (payload or b"").startswith(b"\x1f\x8b")
    )


def _decode_sitemap_payload(
    payload: bytes,
    url: str = "",
    headers: Optional[Dict[str, Any]] = None,
    max_decoded_bytes: int = 52428800,
) -> Tuple[bytes, bool]:
    raw = payload or b""
    if not raw or _looks_like_sitemap_bytes(raw):
        return raw, False
    if not _response_looks_gzipped_sitemap(url, headers, raw):
        return raw, False
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz_file:
            chunks: List[bytes] = []
            total = 0
            while True:
                chunk = gz_file.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_decoded_bytes:
                    raise ValueError("Размер распакованного sitemap превышает 50 МиБ.")
                chunks.append(chunk)
        return b"".join(chunks), True
    except (OSError, EOFError) as exc:
        raise ValueError(f"Не удалось распаковать gzip sitemap: {exc}") from exc
