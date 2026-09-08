"""Tests for IQ4 cloud authentication helpers."""

from urllib.parse import parse_qs, urlparse

import pytest

from pyrainbird_iq4.cloud.auth import (
    CLIENT_ID,
    REDIRECT_URI,
    _build_return_url,
    _extract_antiforgery_token,
    _extract_token,
    _follow_authentication_redirects,
    authenticate,
)
from pyrainbird_iq4.exceptions import (
    RainBirdIQ4AuthenticationError,
    RainBirdIQ4CredentialsError,
    RainBirdIQ4WAFChallengeError,
)


class _ResponseContext:
    def __init__(self, response: "_Response") -> None:
        self.response = response

    async def __aenter__(self) -> "_Response":
        return self.response

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Response:
    def __init__(
        self,
        status: int,
        url: str,
        *,
        location: str = "",
        body: str = "",
    ) -> None:
        self.status = status
        self.url = url
        self.headers = {"Location": location} if location else {}
        self.body = body

    async def text(self) -> str:
        return self.body


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = iter(responses)
        self.urls: list[str] = []

    def get(self, url: str, *, allow_redirects: bool) -> _ResponseContext:
        assert allow_redirects is False
        self.urls.append(url)
        return _ResponseContext(next(self.responses))


def test_extract_access_token() -> None:
    """Test extracting an OIDC access token."""
    value = "https://example.test/#access_token=abc123&token_type=Bearer"

    assert _extract_token(value) == "abc123"


def test_extract_missing_access_token() -> None:
    """Test a response without an access token."""
    assert _extract_token("https://example.test/") is None


def test_extract_antiforgery_token() -> None:
    """Test extracting the login form antiforgery token."""
    html = '<input name="__RequestVerificationToken" type="hidden" value="token123" />'

    assert _extract_antiforgery_token(html) == "token123"


def test_build_return_url() -> None:
    """Test construction of the IQ4 OIDC return URL."""
    result = _build_return_url("state123", "nonce123")

    parsed = urlparse(result)
    query = parse_qs(parsed.query)

    assert parsed.path == "/coreidentityserver/connect/authorize/callback"
    assert query["client_id"] == [CLIENT_ID]
    assert query["redirect_uri"] == [REDIRECT_URI]
    assert query["response_type"] == ["id_token token"]
    assert query["scope"] == ["coreAPI.read coreAPI.write openid profile"]
    assert query["state"] == ["state123"]
    assert query["nonce"] == ["nonce123"]


async def test_follow_authentication_redirects_to_access_token() -> None:
    """Follow redirects and inspect the final response body for a token."""
    location = "/step-one"
    status = 302
    body = ""
    session = _Session(
        [
            _Response(303, "https://identity.test/step-one", location="/callback"),
            _Response(
                200,
                "https://identity.test/callback",
                body="#access_token=token123&token_type=Bearer",
            ),
        ]
    )

    token, final_status = await _follow_authentication_redirects(
        session,
        location,
        status,
        body,
    )

    assert token == "token123"
    assert final_status == 200
    assert session.urls == [
        "https://iq4server.rainbird.com/step-one",
        "https://iq4server.rainbird.com/callback",
    ]


async def test_follow_authentication_redirects_stops_after_ten_redirects() -> None:
    """Do not follow more than ten OIDC redirects."""
    location = "/step-0"
    status = 302
    body = ""
    session = _Session(
        [
            _Response(
                302,
                f"https://identity.test/step-{index}",
                location=f"/step-{index + 1}",
            )
            for index in range(10)
        ]
    )

    with pytest.raises(
        RainBirdIQ4AuthenticationError,
        match="redirect limit exceeded",
    ):
        await _follow_authentication_redirects(session, location, status, body)

    assert len(session.urls) == 10


class _AuthenticationSession:
    def __init__(self, get_response: _Response, post_response: _Response | None = None) -> None:
        self.get_response = get_response
        self.post_response = post_response

    async def __aenter__(self) -> "_AuthenticationSession":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def get(self, _url: str, **_kwargs: object) -> _ResponseContext:
        return _ResponseContext(self.get_response)

    def post(self, _url: str, **_kwargs: object) -> _ResponseContext:
        assert self.post_response is not None
        return _ResponseContext(self.post_response)


async def test_authenticate_classifies_waf_challenge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AWS WAF challenges are distinct from rejected credentials."""
    session = _AuthenticationSession(_Response(202, "https://identity.test/login"))
    monkeypatch.setattr(
        "pyrainbird_iq4.cloud.auth.aiohttp.ClientSession",
        lambda **_kwargs: session,
    )

    with pytest.raises(RainBirdIQ4WAFChallengeError):
        await authenticate("user", "password")


async def test_authenticate_classifies_rejected_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed login that returns 401 is a credential failure."""
    session = _AuthenticationSession(
        _Response(
            200,
            "https://identity.test/login",
            body='<input name="__RequestVerificationToken" value="token123">',
        ),
        _Response(401, "https://identity.test/login"),
    )
    monkeypatch.setattr(
        "pyrainbird_iq4.cloud.auth.aiohttp.ClientSession",
        lambda **_kwargs: session,
    )

    with pytest.raises(RainBirdIQ4CredentialsError):
        await authenticate("user", "password")
