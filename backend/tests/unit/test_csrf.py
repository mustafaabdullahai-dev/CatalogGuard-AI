"""CSRF and security-header tests (D-014).

These run against a real ASGI stack. The middleware's whole job is to inspect
method, path, and headers on the way in and mutate headers on the way out, so a
test that called the class methods directly would be testing the wrong thing:
the cookie parsing and the response-header injection only exist in the wiring.

A bare FastAPI app stands in for the real one. Authentication and the database
are irrelevant to CSRF, and keeping them out means these tests stay fast and
cannot fail for reasons unrelated to what they assert.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from app.core.config import get_settings
from app.core.middleware import CSRFMiddleware, SecurityHeadersMiddleware

PREFIX = "/api/v1"


def _app() -> FastAPI:
    settings = get_settings()
    app = FastAPI()

    @app.post(f"{PREFIX}/thing")
    async def create_thing() -> dict[str, str]:
        return {"created": "yes"}

    @app.get(f"{PREFIX}/thing")
    async def read_thing() -> dict[str, str]:
        return {"read": "yes"}

    @app.post("/outside")
    async def outside() -> dict[str, str]:
        return {"created": "yes"}

    # Added last so they wrap outermost, matching the real application.
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    app.add_middleware(CSRFMiddleware, settings=settings)
    return app


@pytest.fixture
def client() -> httpx.AsyncClient:
    # https, because the CSRF cookie is `Secure` in every environment where it
    # matters. Over http a client correctly refuses to send it back, and the
    # tests would silently stop exercising the "cookie arrives with the request"
    # path that the whole scheme depends on.
    return httpx.AsyncClient(
        transport=ASGITransport(app=_app()), base_url="https://test"
    )


async def _csrf_token(client: httpx.AsyncClient) -> str:
    """Get the client holding a CSRF cookie.

    Read from the client's jar rather than from the response: the middleware
    only sets the cookie when the request arrives without a valid one, so a
    second call correctly returns no `Set-Cookie` at all.
    """
    await client.get(f"{PREFIX}/thing")
    token = client.cookies.get(get_settings().csrf_cookie_name)
    assert token is not None, "a safe request must hand out a token"
    return token


class TestTokenIssuance:
    async def test_safe_request_sets_a_cookie(self, client: httpx.AsyncClient) -> None:
        response = await client.get(f"{PREFIX}/thing")
        assert get_settings().csrf_cookie_name in response.cookies

    async def test_valid_cookie_is_not_reissued(
        self, client: httpx.AsyncClient
    ) -> None:
        """No churn, and nothing to race.

        Re-setting the cookie on every response would overwrite a token the
        client has already copied into a header mid-flight, turning a
        legitimate submission into a 403.
        """
        await client.get(f"{PREFIX}/thing")
        second = await client.get(f"{PREFIX}/thing")
        assert get_settings().csrf_cookie_name not in second.cookies

    async def test_cookie_is_signed_not_just_random(
        self, client: httpx.AsyncClient
    ) -> None:
        """A token this server did not mint must fail validation.

        Plain double-submit is only as strong as the cookie: anyone who can set a
        cookie on the domain chooses both halves. The signature is what makes a
        forged cookie useless.
        """
        from app.core.middleware import CSRFTokens

        tokens = CSRFTokens(get_settings())
        issued = await _csrf_token(client)
        assert tokens.valid(issued)
        assert not tokens.valid("attacker.nonce")
        assert not tokens.valid("")

    async def test_token_is_stable_across_safe_requests(
        self, client: httpx.AsyncClient
    ) -> None:
        """Re-issuing on every GET would invalidate a token already in flight.

        A page holding a token, then opening another request, must not find the
        token it was given no longer accepted.
        """
        first = await _csrf_token(client)
        second = await _csrf_token(client)
        assert first == second


class TestWriteProtection:
    async def test_write_without_token_is_rejected(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(f"{PREFIX}/thing", json={})
        assert response.status_code == 403
        assert "CSRF" in response.json()["detail"]

    async def test_write_with_matching_token_is_allowed(
        self, client: httpx.AsyncClient
    ) -> None:
        token = await _csrf_token(client)
        response = await client.post(
            f"{PREFIX}/thing", json={}, headers={get_settings().csrf_header_name: token}
        )
        assert response.status_code == 200

    async def test_header_without_cookie_is_rejected(
        self, client: httpx.AsyncClient
    ) -> None:
        """A header alone is not proof of anything."""
        response = await client.post(
            f"{PREFIX}/thing",
            json={},
            headers={get_settings().csrf_header_name: "nonce.signature"},
        )
        assert response.status_code == 403

    async def test_cookie_and_header_must_match(
        self, client: httpx.AsyncClient
    ) -> None:
        """The mismatch is the attack.

        This is what happens when a page holds one tenant's cookie while
        submitting a form into another: both halves are individually plausible.
        """
        await _csrf_token(client)
        response = await client.post(
            f"{PREFIX}/thing",
            json={},
            headers={get_settings().csrf_header_name: "other.value"},
        )
        assert response.status_code == 403

    async def test_forged_cookie_and_matching_header_are_rejected(
        self, client: httpx.AsyncClient
    ) -> None:
        """Both halves agreeing is not enough if the cookie is unsigned.

        An attacker who can plant a cookie can also send a matching header. Only
        the signature distinguishes this from a legitimate submission.
        """
        forged = "attacker-chosen-nonce.deadbeef"
        client.cookies.set(get_settings().csrf_cookie_name, forged)
        response = await client.post(
            f"{PREFIX}/thing",
            json={},
            headers={get_settings().csrf_header_name: forged},
        )
        assert response.status_code == 403

    async def test_login_is_protected(self, client: httpx.AsyncClient) -> None:
        """Login CSRF signs the victim into an attacker's account.

        Without this check, whatever the victim does next lands in a catalog the
        attacker controls and can read. The 403 here is raised by the middleware
        before any handler runs, which is the point: the rejection does not
        depend on the login route existing.
        """
        response = await client.post(f"{PREFIX}/auth/login", json={})
        assert response.status_code == 403
        assert "CSRF" in response.json()["detail"]

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    async def test_every_state_changing_method_is_covered(
        self, client: httpx.AsyncClient, method: str
    ) -> None:
        """An unguarded method is an unguarded endpoint.

        Restricting the check to POST is the classic mistake: a DELETE that
        revokes a membership is just as state-changing.
        """
        call = getattr(client, method)
        # `delete` takes no body; the others do. Both must still be refused.
        response = (
            await call(f"{PREFIX}/thing")
            if method == "delete"
            else await call(f"{PREFIX}/thing", json={})
        )
        assert response.status_code == 403

    async def test_non_api_paths_are_not_guarded(
        self, client: httpx.AsyncClient
    ) -> None:
        """The API prefix is the boundary.

        Without the prefix check, a form on any other page of the host would need
        a token, and the check would be useless because that page would issue one.
        """
        assert (await client.post("/outside", json={})).status_code == 200


class TestSecurityHeaders:
    @pytest.mark.parametrize(
        ("header", "value"),
        [
            ("x-content-type-options", "nosniff"),
            ("x-frame-options", "DENY"),
            ("referrer-policy", "no-referrer"),
            ("cross-origin-opener-policy", "same-origin"),
        ],
    )
    async def test_header_present(
        self, client: httpx.AsyncClient, header: str, value: str
    ) -> None:
        response = await client.get(f"{PREFIX}/thing")
        assert response.headers[header] == value

    async def test_authenticated_responses_are_not_cacheable(
        self, client: httpx.AsyncClient
    ) -> None:
        """`no-store` on every response.

        A shared cache holding an authenticated response would serve one
        operator's catalog to the next person through the same proxy.
        """
        response = await client.get(f"{PREFIX}/thing")
        assert response.headers["cache-control"] == "no-store"

    async def test_hsts_absent_outside_production(
        self, client: httpx.AsyncClient
    ) -> None:
        """Sending HSTS from a development origin over HTTP is meaningless.

        Worse, browsers cache it: a `localhost` that once advertised HSTS stays
        pinned to HTTPS and the developer's own server becomes unreachable.
        """
        response = await client.get(f"{PREFIX}/thing")
        assert "strict-transport-security" not in response.headers
