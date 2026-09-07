"""Sandbox-bound evaluation entry point for the unchanged ShopMate buyer runtime."""

from __future__ import annotations

import argparse
import json
import re
import time
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import ClassVar
from urllib.parse import quote

import httpx
import jwt
from fastapi import HTTPException
from shopmate.app import create_app
from shopmate.auth import SHOPPING_SCOPES, AuthClient, RequestIdentity
from shopmate.buyer_client import BuyerClient
from shopmate.provider import Provider
from shopmate.sessions import SessionStore
from shopmate.settings import Settings


def _sandbox_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise ValueError("A fixed evaluation sandbox identifier is required")
    return value


class EvalAuth(AuthClient):
    def __init__(self, settings, sandbox_id: str, http_client=None):
        self.sandbox_id = _sandbox_id(sandbox_id)
        super().__init__(settings, http_client)

    async def verify(self, token: str, *, role: str = "merchant") -> RequestIdentity:
        if role != "buyer":
            raise HTTPException(403, "Only evaluation buyers are available")
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if header.get("alg") != "RS256" or not isinstance(kid, str) or not kid:
                raise ValueError()
        except (jwt.PyJWTError, ValueError, TypeError):
            raise HTTPException(401, "Invalid evaluation user token") from None
        if time.monotonic() >= self._keys_until or kid not in self._keys:
            await self._refresh_keys()
        if kid not in self._keys:
            raise HTTPException(401, "Invalid evaluation user token")
        try:
            claims = jwt.decode(
                token,
                self._keys[kid],
                algorithms=["RS256"],
                issuer=self.settings.issuer,
                audience=self.settings.user_audience,
                options={"require": ["exp", "iat", "nbf", "iss", "aud", "sub", "jti"]},
            )
            subject = claims["sub"]
            permissions = claims.get("permissions")
            handle = claims.get("evaluation_handle")
            if (
                claims["aud"] not in (self.settings.user_audience, [self.settings.user_audience])
                or not isinstance(subject, str)
                or not subject.strip()
                or len(subject) > 128
                or claims.get("token_type") != "eval_direct_user"
                or claims.get("principal_state") != "ACTIVE"
                or claims.get("sandbox") != self.sandbox_id
                or not isinstance(handle, str)
                or re.fullmatch(r"[A-Za-z0-9_-]{43}", handle) is None
                or any(key in claims for key in ("act", "session", "eval_sandbox"))
                or not isinstance(claims["jti"], str)
                or not claims["jti"].strip()
                or any(type(claims[key]) is not int for key in ("exp", "iat", "nbf"))
                or claims["exp"] <= max(claims["iat"], claims["nbf"])
                or not isinstance(permissions, list)
                or not all(isinstance(permission, str) for permission in permissions)
            ):
                raise ValueError()
        except (jwt.PyJWTError, ValueError, TypeError):
            raise HTTPException(401, "Invalid evaluation user token") from None
        if "shopping:session:create" not in permissions:
            raise HTTPException(403, "Buyer permission required")
        # A valid cached signing key does not prove that the trial is still active.
        try:
            response = await self.http.post(
                self.settings.commerce_url.rstrip("/")
                + "/internal/eval/sandboxes/"
                + quote(self.sandbox_id, safe="")
                + "/liveness",
                headers={"Authorization": "Bearer " + token, "X-Eval-Sandbox-Id": self.sandbox_id},
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise HTTPException(503, "Evaluation liveness unavailable") from None
        if response.status_code in (401, 403, 404):
            raise HTTPException(403, "Evaluation sandbox is not active")
        if response.status_code != 204:
            raise HTTPException(503, "Evaluation liveness unavailable")
        return RequestIdentity(subject, token)

    async def login(self, login_identifier, password, *, role="merchant"):
        raise HTTPException(403, "Use an issued evaluation identity")

    async def exchange(self, identity, session_id, scope):
        raise HTTPException(403, "Merchant delegation is unavailable")

    async def exchange_shopping(
        self, identity: RequestIdentity, session_id: str, scope: str
    ) -> str:
        if scope not in SHOPPING_SCOPES:
            raise ValueError("Unsupported shopping scope")
        result = await self._request(
            "POST",
            self.settings.auth_url.rstrip("/") + "/auth/token/exchange",
            auth=httpx.BasicAuth("shopping-agent", self.settings.shopping_service_secret),
            headers={
                "X-User-Authorization": "Bearer " + identity.token,
                "X-Eval-Sandbox-Id": self.sandbox_id,
            },
            json={"sessionId": session_id, "userSubject": identity.subject, "scope": scope},
        )
        token = result.get("accessToken")
        if not isinstance(token, str) or not token:
            raise HTTPException(503, "Invalid identity response")
        return token


class EvalBuyerClient(BuyerClient):
    _READ_PATHS: ClassVar[dict[str, str]] = {
        "/internal/shopping/orders": "/internal/eval/shopping/orders",
        "/internal/shopping/preferences": "/internal/eval/shopping/preferences",
        "/internal/shopping/cart": "/internal/eval/shopping/cart",
        "/api/retail/policies": "/internal/eval/shopping/policies",
    }

    def __init__(self, base_url: str, sandbox_id: str, http_client=None):
        self.sandbox_id = _sandbox_id(sandbox_id)
        super().__init__(base_url, http_client)

    async def _request(self, method, path, token=None, *, headers=None, **kwargs):
        if method == "GET":
            if path in self._READ_PATHS:
                path = self._READ_PATHS[path]
            elif path.startswith("/internal/shopping/orders/"):
                path = "/internal/eval/shopping/orders/" + path.removeprefix(
                    "/internal/shopping/orders/"
                )
        # BuyerClient removes ambient identity headers before merging this explicit mapping.
        supplied = httpx.Headers(headers)
        supplied["X-Eval-Sandbox-Id"] = self.sandbox_id
        return await super()._request(method, path, token, headers=supplied, **kwargs)


class _UnavailableMerchant:
    def __getattr__(self, name):
        raise RuntimeError("Merchant operations are unavailable in buyer evaluation")


def create_evaluation_app(settings, sandbox_id, *, auth=None, buyer_client=None, provider=None):
    sandbox_id = _sandbox_id(sandbox_id)
    unavailable = _UnavailableMerchant()
    app = create_app(
        settings,
        auth=auth,
        buyer_client=buyer_client,
        provider=provider,
        backend=unavailable,
        agent=unavailable,
        sandbox=unavailable,
    )
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not getattr(route, "path", "").startswith("/api/merchant")
    ]
    product_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with AsyncExitStack() as owned:
            # Each process owns one trial database; never open or migrate a previous host's state.
            path = Path(settings.state_path).expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(mode=0o600, exist_ok=False)
            store = SessionStore(path)
            owned.callback(store.close)
            resources = application.state.resources
            resources["store"] = store
            if auth is None:
                resources["auth"] = EvalAuth(settings, sandbox_id)
                owned.push_async_callback(resources["auth"].close)
            if buyer_client is None:
                resources["buyer_client"] = EvalBuyerClient(settings.commerce_url, sandbox_id)
                owned.push_async_callback(resources["buyer_client"].close)
            if provider is None:
                resources["provider"] = Provider(settings)
                owned.push_async_callback(resources["provider"].close)
            async with product_lifespan(application):
                yield

    app.router.lifespan_context = lifespan
    return app


def main():
    parser = argparse.ArgumentParser(description="Run one sandbox-bound ShopMate buyer host")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--sandbox", required=True)
    parser.add_argument("--port", type=int, default=0)
    arguments = parser.parse_args()
    try:
        if not 0 <= arguments.port <= 65535:
            raise ValueError()
        values = json.loads(arguments.config.read_text())
        if not isinstance(values, dict):
            raise TypeError()
        for name in ("citybuddy_dir", "state_path"):
            if name in values:
                values[name] = Path(values[name]).expanduser().resolve()
        settings = Settings(**values)
        app = create_evaluation_app(settings, arguments.sandbox)
    except (OSError, ValueError, TypeError):
        raise SystemExit("Evaluation host configuration is invalid") from None
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=arguments.port, access_log=False)


if __name__ == "__main__":
    main()
