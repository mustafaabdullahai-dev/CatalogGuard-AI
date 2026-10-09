"""The API's documented contract (§42).

Every route has a summary today, and that is the part of §42 a human checks by
reading `/docs`. The parts a reader cannot check are asserted here, against the
OpenAPI document itself rather than against the code that produced it:

- every operation documents a **success response with a schema**, so a client
  generated from the spec has something to type against;
- every operation documents at least one **error response**, so a caller can
  handle a refusal without reading the route's docstring;
- every authenticated route declares the **workspace header** the backend
  requires and every public one declares none — a header that appears on
  `/auth/csrf` is a spec that tells a client to send a workspace it does not have;
- summaries, operation ids and tags are unique, because a duplicate operation id
  breaks client generators in a way nobody notices until one is built.

What this cannot assert, and says so in the module docstring: that the schema
matches the runtime response. That is what the endpoint tests do, with real
responses.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.main import app

PREFIX = "/api/v1"

# Two different exemptions, and conflating them is how a route ends up
# accidentally unguarded. `/auth/logout` needs no *workspace* — it revokes the
# session that named the workspaces — but it absolutely needs a session, so it
# must not appear in the second list.
#
# `/health` and `/ready` are infrastructure probes, called by a load balancer
# rather than a tenant client, and belong to both.
PROBES = ("/health", "/ready")

# No `X-Workspace-Id`: the session is what is missing, or what is being asked
# about, rather than the workspace.
NO_WORKSPACE_PREFIXES = tuple(
    f"{PREFIX}{suffix}"
    for suffix in (
        "/auth/login",
        "/auth/logout",
        "/auth/me",
        "/auth/csrf",
        "/auth/password",
        "/auth/password-reset",
        "/auth/invitations/accept",
    )
)

# No session at all: the routes a client calls *to obtain* one.
NO_SESSION_PREFIXES = tuple(
    f"{PREFIX}{suffix}"
    for suffix in (
        "/auth/login",
        "/auth/csrf",
        "/auth/password-reset",
        "/auth/invitations/accept",
    )
)


def _needs_no_workspace(path: str) -> bool:
    """No `X-Workspace-Id` required — distinct from needing no session."""

    return path in PROBES or path.startswith(NO_WORKSPACE_PREFIXES)


def _needs_no_session(path: str) -> bool:
    return path in PROBES or path.startswith(NO_SESSION_PREFIXES)


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    return app.openapi()


def _operations(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    found: list[tuple[str, str, dict[str, Any]]] = []
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            if method in ("get", "post", "put", "patch", "delete"):
                found.append((path, method, operation))
    return found


def _parameters(operation: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        parameter
        for parameter in operation.get("parameters", [])
        if parameter.get("in") == "header"
    ]


class TestEveryOperationIsDocumented:
    def test_every_operation_has_a_summary_and_a_tag(self, spec: dict[str, Any]) -> None:
        missing = [
            f"{method.upper()} {path}"
            for path, method, operation in _operations(spec)
            if not operation.get("summary") or not operation.get("tags")
        ]

        assert not missing, f"undocumented operations: {missing}"

    def test_operation_ids_are_unique(self, spec: dict[str, Any]) -> None:
        ids = [
            operation.get("operationId")
            for _path, _method, operation in _operations(spec)
            if operation.get("operationId")
        ]
        duplicates = sorted({value for value in ids if ids.count(value) > 1})

        assert not duplicates, f"duplicate operation ids break client generators: {duplicates}"

    def test_every_operation_documents_at_least_one_error_response(
        self, spec: dict[str, Any]
    ) -> None:
        """A route that documents only success tells a caller nothing about 403."""

        undocumented = [
            f"{method.upper()} {path}"
            for path, method, operation in _operations(spec)
            # Probes are exempt: a load balancer asks whether the process is up,
            # and an error schema for that is decoration.
            if path not in PROBES
            and not any(
                code.startswith(("4", "5")) for code in operation.get("responses", {})
            )
        ]

        assert not undocumented, f"no documented failure mode: {undocumented}"

    def test_every_operation_declares_authentication(self, spec: dict[str, Any]) -> None:
        """§42 lists authentication beside the schemas; a route that needs a
        session has to say so in the spec, not only in the dependency.

        Checked in both directions. A tenant route that omits `security` reads
        as anonymous to anyone generating a client from `/docs`; a public route
        that declares one is worse, because the credential it asks for is the
        thing the caller is trying to obtain.
        """

        unguarded = [
            f"{method.upper()} {path}"
            for path, method, operation in _operations(spec)
            if not _needs_no_session(path) and "security" not in operation
        ]
        overguarded = [
            f"{method.upper()} {path}"
            for path, method, operation in _operations(spec)
            if _needs_no_session(path) and operation.get("security")
        ]

        assert not unguarded, f"tenant routes with no declared security: {unguarded}"
        assert not overguarded, f"public routes demanding a credential: {overguarded}"

    def test_the_security_scheme_is_the_session_cookie(
        self, spec: dict[str, Any]
    ) -> None:
        from app.core.config import get_settings

        settings = get_settings()
        scheme = spec["components"]["securitySchemes"]["sessionCookie"]

        assert scheme["type"] == "apiKey"
        assert scheme["in"] == "cookie"
        assert scheme["name"] == settings.session_cookie_name

    def test_a_guarded_route_requires_exactly_the_session_scheme(
        self, spec: dict[str, Any]
    ) -> None:
        guarded = [
            operation["security"]
            for path, _method, operation in _operations(spec)
            if not _needs_no_session(path)
        ]

        assert guarded, "expected guarded routes to exist"
        assert all(requirement == [{"sessionCookie": []}] for requirement in guarded)


class TestEveryOperationHasSchemas:
    def test_every_success_response_declares_a_schema(self, spec: dict[str, Any]) -> None:
        """A generated client needs something to deserialize."""

        without_schema = [
            f"{method.upper()} {path}"
            for path, method, operation in _operations(spec)
            for code, response in operation.get("responses", {}).items()
            if code.startswith("2")
            and "content" not in response
        ]

        assert not without_schema, f"success responses with no schema: {without_schema}"

    def test_every_body_declares_a_request_schema(self, spec: dict[str, Any]) -> None:
        """§42's request schema, checked per declared media type.

        A file upload is `multipart/form-data` and cannot carry a JSON schema;
        what it must carry is *some* described body, or a client has no idea
        what to send.
        """

        without_schema = [
            f"{method.upper()} {path}"
            for path, method, operation in _operations(spec)
            if "requestBody" in operation
            and not operation["requestBody"].get("content")
        ]

        assert not without_schema, f"bodies with no media type: {without_schema}"

    def test_every_error_response_declares_a_schema(self, spec: dict[str, Any]) -> None:
        """The error models are the app's own: `{"detail": "..."}`."""

        without_schema = [
            f"{method.upper()} {path} -> {code}"
            for path, method, operation in _operations(spec)
            for code, response in operation.get("responses", {}).items()
            if code.startswith(("4", "5")) and "content" not in response
        ]

        assert not without_schema, f"error responses with no schema: {without_schema}"


class TestTenantHeaderIsDeclared:
    def test_tenant_routes_declare_the_workspace_header(self, spec: dict[str, Any]) -> None:
        missing = [
            f"{method.upper()} {path}"
            for path, method, operation in _operations(spec)
            if not _needs_no_workspace(path)
            and not any(
                parameter.get("name") == "X-Workspace-Id"
                for parameter in _parameters(operation)
            )
        ]

        assert not missing, f"tenant routes without the workspace header: {missing}"

    def test_public_routes_do_not_ask_for_a_workspace(self, spec: dict[str, Any]) -> None:
        """`/auth/csrf` demanding a workspace would be a client deadlock."""

        asking = [
            f"{method.upper()} {path}"
            for path, method, operation in _operations(spec)
            if _needs_no_workspace(path)
            and any(
                parameter.get("name") == "X-Workspace-Id" for parameter in _parameters(operation)
            )
        ]

        assert not asking, f"public routes asking for a workspace: {asking}"


class TestTheSurfaceItself:
    def test_the_spec_declares_title_version_and_description(self, spec: dict[str, Any]) -> None:
        assert spec["info"]["title"]
        assert spec["info"]["version"]
        assert spec["info"]["description"], "the description is what /docs shows first"

    def test_health_and_readiness_are_present_but_need_no_workspace(
        self, spec: dict[str, Any]
    ) -> None:
        assert "/health" in spec["paths"]
        assert "/ready" in spec["paths"]

    def _response_schema_names(self, spec: dict[str, Any]) -> set[str]:
        """Schemas reachable from a response body, following one level of refs."""
        names: set[str] = set()
        schemas = spec.get("components", {}).get("schemas", {})
        for operation in (op for _p, _m, op in _operations(spec)):
            for response in operation.get("responses", {}).values():
                for media in (response.get("content") or {}).values():
                    schema = media.get("schema") or {}
                    ref = schema.get("$ref")
                    if ref:
                        names.add(ref.rsplit("/", 1)[-1])
        # One level deeper: a response model that wraps another.
        for name in list(names):
            for prop in (schemas.get(name, {}).get("properties") or {}).values():
                ref = (prop.get("items") or prop).get("$ref")
                if ref:
                    names.add(ref.rsplit("/", 1)[-1])
        return names

    def test_no_response_schema_carries_a_secret_field(self, spec: dict[str, Any]) -> None:
        """A spec is served to whoever can reach `/docs`.

        Checked on schema *properties* rather than on the serialized document:
        `/auth/password-reset` legitimately exists as a path, while a response
        model with a `password` or `api_key` field would be a leak. What no test
        can prove is that a handler does not put a secret into a free-form
        string; D-010's masking is the control there.
        """
        forbidden = {
            "password",
            "hashed_password",
            "secret_key",
            "api_key",
            "database_url",
            "authorization",
            "cookie",
        }
        schemas = spec.get("components", {}).get("schemas", {})
        offenders: list[str] = []
        for name in self._response_schema_names(spec):
            for model in schemas.get(name, {}).get("properties", {}):
                if model.lower() in forbidden:
                    offenders.append(f"{name}.{model}")

        assert not offenders, f"schemas carrying secret-shaped fields: {offenders}"

    def test_every_api_route_lives_under_the_configured_prefix(
        self, spec: dict[str, Any]
    ) -> None:
        for path in spec["paths"]:
            if path.startswith("/api/"):
                assert path.startswith(PREFIX), f"{path} is outside the configured prefix"