"""Authentication for the Rain Bird IQ4 cloud service."""

from __future__ import annotations

import re
import secrets
from urllib.parse import quote, urlencode, urljoin

import aiohttp

from ..exceptions import (
    RainBirdIQ4AuthenticationError,
    RainBirdIQ4ConnectionError,
    RainBirdIQ4CredentialsError,
    RainBirdIQ4WAFChallengeError,
    safe_error_detail,
)

AUTH_BASE = "https://iq4server.rainbird.com/coreidentityserver"
CLIENT_ID = "C5A6F324-3CD3-4B22-9F78-B4835BA55D25"
REDIRECT_URI = "https://iq4.rainbird.com/auth.html"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

TOKEN_RE = re.compile(r'access_token=([^&"]+)')
ANTIFORGERY_RE = re.compile(
    r'__RequestVerificationToken.*?value="([^"]+)"',
    re.IGNORECASE | re.DOTALL,
)


def _random_hex(byte_count: int = 8) -> str:
    """Return a cryptographically secure hexadecimal string."""
    return secrets.token_hex(byte_count)


def _extract_token(value: str) -> str | None:
    """Extract the access token from a redirect URL or response body."""
    match = TOKEN_RE.search(value)
    return match.group(1) if match else None


def _extract_antiforgery_token(html: str) -> str | None:
    """Extract the ASP.NET antiforgery token from the login page."""
    match = ANTIFORGERY_RE.search(html)
    return match.group(1) if match else None


def _build_return_url(state: str, nonce: str) -> str:
    """Build the OIDC authorize callback URL."""
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "id_token token",
        "scope": "coreAPI.read coreAPI.write openid profile",
        "state": state,
        "nonce": nonce,
    }

    return "/coreidentityserver/connect/authorize/callback?" + urlencode(
        params,
        quote_via=quote,
    )


async def _follow_authentication_redirects(
    session: aiohttp.ClientSession,
    location: str,
    status: int,
    body: str,
) -> tuple[str | None, int]:
    """Follow the IQ4 OIDC redirect chain and return its token and status."""
    for redirect_count in range(10):
        if token := _extract_token(location) or _extract_token(body):
            return token, status

        if status not in (301, 302, 303) or not location:
            return None, status

        redirect_url = urljoin("https://iq4server.rainbird.com", location)
        async with session.get(redirect_url, allow_redirects=False) as response:
            location = response.headers.get("Location", "")
            body = await response.text()
            status = response.status

        if redirect_count == 9 and status in (301, 302, 303) and location:
            raise RainBirdIQ4AuthenticationError(
                "IQ4 authentication redirect limit exceeded (10 redirects)"
            )

    return None, status


async def authenticate(
    username: str,
    password: str,
    *,
    timeout: float = 20.0,
) -> str:
    """Authenticate with IQ4 and return an access token."""
    state = _random_hex()
    nonce = _random_hex()

    return_url = _build_return_url(state, nonce)

    login_url = f"{AUTH_BASE}/Account/Login?ReturnUrl={quote(return_url, safe='')}"

    headers = {
        "User-Agent": USER_AGENT,
    }

    client_timeout = aiohttp.ClientTimeout(total=timeout)

    try:
        async with aiohttp.ClientSession(
            timeout=client_timeout,
            headers=headers,
        ) as session:
            async with session.get(login_url) as response:
                if response.status == 202:
                    raise RainBirdIQ4WAFChallengeError(
                        "IQ4 login was blocked by an AWS WAF challenge"
                    )

                if response.status >= 400:
                    raise RainBirdIQ4AuthenticationError(
                        f"IQ4 login returned an unexpected HTTP response (HTTP {response.status})"
                    )

                login_html = await response.text()

            antiforgery = _extract_antiforgery_token(login_html)

            if antiforgery is None:
                raise RainBirdIQ4AuthenticationError("Unable to find IQ4 login antiforgery token")

            form = {
                "Username": username,
                "Password": password,
                "ReturnUrl": return_url,
                "__RequestVerificationToken": antiforgery,
            }

            async with session.post(
                login_url,
                data=form,
                allow_redirects=False,
            ) as response:
                if response.status == 202:
                    raise RainBirdIQ4WAFChallengeError(
                        "IQ4 login was blocked by an AWS WAF challenge"
                    )

                if response.status >= 400 and response.status not in (401, 403):
                    raise RainBirdIQ4AuthenticationError(
                        f"IQ4 login returned an unexpected HTTP response (HTTP {response.status})"
                    )

                location = response.headers.get("Location", "")
                body = await response.text()
                post_status = response.status

            token, status = await _follow_authentication_redirects(
                session,
                location,
                post_status,
                body,
            )

            if token:
                return token

            if status in (401, 403):
                raise RainBirdIQ4CredentialsError("IQ4 username or password was rejected")

            raise RainBirdIQ4AuthenticationError(
                "IQ4 authentication returned an unexpected HTTP response "
                f"(HTTP {status}) without an access token"
            )

    except RainBirdIQ4AuthenticationError:
        raise
    except (aiohttp.ClientError, TimeoutError, OSError) as exc:
        raise RainBirdIQ4ConnectionError(
            f"IQ4 connection failed: {type(exc).__name__}: {safe_error_detail(exc)}"
        ) from exc
