"""Asynchronous Rain Bird IQ4 client."""

from __future__ import annotations

import ssl
from dataclasses import dataclass

import aiohttp

from .exceptions import RainBirdIQ4ConnectionError


@dataclass(slots=True)
class ProbeResponse:
    """Result of an HTTP probe."""

    url: str
    status: int
    reason: str
    headers: dict[str, str]
    content_type: str | None
    body: str


class RainBirdIQ4Client:
    """Client for communicating with a Rain Bird IQ4 controller."""

    def __init__(
        self,
        host: str,
        *,
        port: int = 443,
        verify_ssl: bool = True,
        timeout: float = 10.0,
    ) -> None:
        """Initialize the IQ4 client."""
        self.host = host
        self.port = port
        self.verify_ssl = verify_ssl
        self.timeout = timeout

    @property
    def base_url(self) -> str:
        """Return the controller base URL."""
        if self.port == 443:
            return f"https://{self.host}"
        return f"https://{self.host}:{self.port}"

    def _ssl_context(self) -> ssl.SSLContext | bool:
        if self.verify_ssl:
            return ssl.create_default_context()

        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    async def probe(self, path: str = "/") -> ProbeResponse:
        """Perform a read-only HTTP GET request."""
        if not path.startswith("/"):
            path = f"/{path}"

        url = f"{self.base_url}{path}"

        timeout = aiohttp.ClientTimeout(total=self.timeout)

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session, session.get(
                url,
                ssl=self._ssl_context(),
                allow_redirects=False,
            ) as response:
                body = await response.text(errors="replace")

                return ProbeResponse(
                    url=str(response.url),
                    status=response.status,
                    reason=response.reason or "",
                    headers=dict(response.headers),
                    content_type=response.content_type,
                    body=body,
                )

        except (
            aiohttp.ClientError,
            TimeoutError,
            OSError,
            ssl.SSLError,
        ) as exc:
            raise RainBirdIQ4ConnectionError(
                f"Unable to connect to IQ4 controller at {url}: {exc}"
            ) from exc
