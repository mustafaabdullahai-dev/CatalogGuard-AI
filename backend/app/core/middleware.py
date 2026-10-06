"""Security middleware (D-014, Section 33).

Two ASGI middleware classes, both pure ASGI rather than `BaseHTTPMiddleware`
because they need no request body and no streaming support. BaseHTTPMiddleware
buffers bodies and has a history of subtle bugs around disconnects; there is no
reason to accept either for a header check and a cookie comparison.

`CSRFMiddleware` uses a signed double-submit token. The plain double-submit
pattern compares a cookie against a header, which is only as strong as the
cookie: an attacker who can set a cookie on the domain (a subdomain
takeover, or a sibling application on the same registrable domain) chooses both
halves. Signing the nonce with a server secret means a forged cookie cannot
produce a matching header.
"""

from __future__ import annotations

import hmac
import secrets
from hashlib import sha256

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

# Paths that legitimately have no cookie yet. A client that calls one of these
# first can log in without a preflight round trip.
CSRF_EXEMPT_PATHS = frozenset({"/health", "/ready"})


class SecurityHeadersMiddleware:
    """Response headers that are correct by default and cheap to keep that way."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        headers = {
            "x-content-type-options": "nosniff",
            # The command center is not embeddable. Deny rather than
            # same-origin: framing a catalog UI inside an attacker's page is
            # clickjacking.
            "x-frame-options": "DENY",
            "referrer-policy": "no-referrer",
            # Authenticated responses must never sit in a shared cache.
            "cache-control": "no-store",
            "cross-origin-opener-policy": "same-origin",
        }
        if settings.is_production:
            # Only meaningful over TLS, and only safe to send from production.
            headers["strict-transport-security"] = (
                "max-age=31536000; includeSubDomains"
            )
        self.headers = headers

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                mutable = MutableHeaders(scope=message)
                for key, value in self.headers.items():
                    mutable[key] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)


class CSRFTokens:
    """Mint and check signed double-submit tokens.

    Separated from the middleware so the `/auth/csrf` endpoint can hand back a
    token the caller can actually use. Two implementations of the same signing
    scheme would drift, and the failure mode of that drift is a 403 on every
    write with nothing in the logs to explain it.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.secret = settings.jwt_secret.encode()
        self.cookie_name = settings.csrf_cookie_name
        self.header_name = settings.csrf_header_name

    def issue(self) -> str:
        """`nonce.hmac`. The nonce is random per cookie; the signature binds it.

        Signing is what makes this more than a plain double submit: a token the
        server did not mint can never compare equal to a header value, even if
        an attacker managed to set the cookie.
        """
        nonce = secrets.token_urlsafe(32)
        digest = hmac.new(self.secret, nonce.encode(), sha256).hexdigest()
        return f"{nonce}.{digest}"

    def valid(self, value: str | None) -> bool:
        if not value or "." not in value:
            return False
        nonce, _, _digest = value.rpartition(".")
        return hmac.compare_digest(self.issue_for(nonce), value)

    def issue_for(self, nonce: str) -> str:
        digest = hmac.new(self.secret, nonce.encode(), sha256).hexdigest()
        return f"{nonce}.{digest}"

    def matches(self, cookie_value: str | None, header_value: str | None) -> bool:
        """Both halves present, cookie genuine, and the two identical."""
        return bool(
            cookie_value
            and header_value
            and self.valid(cookie_value)
            and hmac.compare_digest(cookie_value, header_value)
        )

    def cookie_header(self, value: str) -> str:
        return (
            f"{self.cookie_name}={value}; Path=/; "
            f"SameSite={self.settings.session_cookie_samesite}"
            f"{'; Secure' if self.settings.cookie_secure else ''}"
        )


class CSRFMiddleware:
    """Reject state-changing API calls that cannot prove they are same-origin."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self.tokens = CSRFTokens(settings)
        self.api_prefix = settings.api_prefix

    def _cookie_header(self, value: str) -> str:
        return self.tokens.cookie_header(value)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        path: str = scope.get("path", "")
        method: str = scope.get("method", "GET").upper()
        cookie_token = _cookie_value(
            headers.get("cookie"), self.tokens.cookie_name
        )

        if method in SAFE_METHODS or path in CSRF_EXEMPT_PATHS:
            # Hand out a token on any safe request so a fresh client can
            # authenticate itself before its first POST.
            if not self.tokens.valid(cookie_token):
                token = self.tokens.issue()
                # Put the cookie into the request scope as well as the response.
                # Without this, a handler that reads `request.cookies` sees no
                # token and mints a second one, so the client ends up with two
                # Set-Cookie headers and whichever arrives last silently
                # invalidates the other.
                _inject_cookie(scope, self.tokens.cookie_name, token)
                await self._respond_with_cookie(scope, receive, send, token)
                return
            await self.app(scope, receive, send)
            return

        if not path.startswith(self.api_prefix):
            await self.app(scope, receive, send)
            return

        header_token = headers.get(self.tokens.header_name)
        if not self.tokens.matches(cookie_token, header_token):
            await self._reject(send)
            return

        await self.app(scope, receive, send)

    async def _respond_with_cookie(
        self, scope: Scope, receive: Receive, send: Send, token: str
    ) -> None:
        """Serve the request, adding the CSRF cookie to the response."""
        sent = False

        async def send_with_cookie(message: Message) -> None:
            nonlocal sent
            if message["type"] == "http.response.start" and not sent:
                sent = True
                mutable = MutableHeaders(scope=message)
                mutable.append("set-cookie", self._cookie_header(token))
            await send(message)

        await self.app(scope, receive, send_with_cookie)

    async def _reject(self, send: Send) -> None:
        body = (
            b'{"detail":"CSRF token missing or invalid. '
            b"GET a URL first to obtain one, then send it in the "
            b'X-CSRF-Token header."}'
        )
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def _inject_cookie(scope: Scope, name: str, value: str) -> None:
    """Add `name=value` to the request's Cookie header, in place.

    Rewrites the scope header list rather than the response, so every downstream
    component reads the same cookie the client will send back.
    """
    headers = MutableHeaders(scope=scope)
    existing = headers.get("cookie")
    if existing:
        headers["cookie"] = f"{existing}; {name}={value}"
    else:
        headers["cookie"] = f"{name}={value}"


def _cookie_value(raw: str | None, name: str) -> str | None:
    if not raw:
        return None
    for part in raw.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return None
