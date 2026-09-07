"""Local HTTP/model doubles exercise the installed ShopMate factory, never a real model."""

from __future__ import annotations

import base64
import json
import sqlite3
import time
from uuid import UUID

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from shopmate.auth import AuthClient, RequestIdentity
from shopmate.buyer_backend import CityBuddyStorefrontBackend
from shopmate.provider import Provider, current_budget
from shopmate.providers.chat_to_messages import make_client
from shopmate.settings import Settings
from shopping_agent_runtime import ShoppingAgent

from stateeval import shopmate_host as host

SANDBOX = "evaluation-one"
ORDER = "d554986f-58ed-43dd-b639-1a94d9844501"
PENDING = "2716865f-7414-47aa-a9c5-56fef4d78006"


@pytest.fixture(scope="module")
def signer():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(signer, **changes):
    now = int(time.time())
    claims = {
        "sub": "evaluation-buyer",
        "iss": "https://identity.citybuddy.test",
        "aud": "citybuddy-web",
        "iat": now,
        "nbf": now,
        "exp": now + 300,
        "jti": "evaluation-token",
        "token_type": "eval_direct_user",
        "principal_state": "ACTIVE",
        "permissions": ["support:chat", "shopping:session:create"],
        "sandbox": SANDBOX,
        "evaluation_handle": "h" * 43,
    }
    claims.update(changes)
    return jwt.encode(claims, signer, algorithm="RS256", headers={"kid": "current"})


def transport(signer, requests, handler=None):
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signer.public_key())) | {"kid": "current"}

    def respond(request):
        requests.append(request)
        if request.url.path == "/auth/jwks":
            return httpx.Response(200, json={"keys": [jwk]})
        if handler is not None:
            return handler(request)
        assert request.url.path == f"/internal/eval/sandboxes/{SANDBOX}/liveness"
        assert request.method == "POST"
        return httpx.Response(204)

    return httpx.MockTransport(respond)


@pytest.mark.asyncio
async def test_signed_identity_calls_liveness_on_every_request_and_revocation_denies(signer):
    requests = []
    alive = True

    def respond(request):
        assert request.headers["x-eval-sandbox-id"] == SANDBOX
        assert request.method == "POST" and request.content == b""
        return httpx.Response(204 if alive else 403, text="private diagnostic")

    async with httpx.AsyncClient(transport=transport(signer, requests, respond)) as client:
        auth = host.EvalAuth(Settings(), SANDBOX, client)
        value = token(signer, aud=["citybuddy-web"])
        assert await auth.verify(value, role="buyer") == RequestIdentity("evaluation-buyer", value)
        assert "evaluation-token" not in repr(await auth.verify(value, role="buyer"))
        alive = False
        with pytest.raises(HTTPException) as error:
            await auth.verify(value, role="buyer")
        assert error.value.status_code == 403
        assert "private diagnostic" not in error.value.detail
        assert sum(r.url.path.endswith("/liveness") for r in requests) == 3
        assert sum(r.url.path == "/auth/jwks" for r in requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"token_type": "direct_user"},
        {"sandbox": "another-sandbox"},
        {"evaluation_handle": "h" * 42},
        {"principal_state": "DISABLED"},
        {"act": {"azp": "shopping-agent"}},
        {"session": "spoofed"},
        {"eval_sandbox": SANDBOX},
        {"aud": ["citybuddy-web", "other"]},
        {"iss": "https://other.invalid"},
        {"sub": "x" * 129},
        {"jti": ""},
        {"exp": 1},
        {"nbf": int(time.time()) + 3600},
        {"iat": int(time.time()) + 3600},
        {"nbf": None},
        {"iat": True},
    ],
)
async def test_evaluation_claim_boundary_rejects_before_liveness(signer, change):
    requests = []
    async with httpx.AsyncClient(transport=transport(signer, requests)) as client:
        auth = host.EvalAuth(Settings(), SANDBOX, client)
        with pytest.raises(HTTPException) as error:
            await auth.verify(token(signer, **change), role="buyer")
        assert error.value.status_code == 401
        assert not any(r.url.path.endswith("/liveness") for r in requests)


@pytest.mark.asyncio
async def test_production_auth_remains_closed_and_role_permission_and_signature_are_required(
    signer,
):
    requests = []
    async with httpx.AsyncClient(transport=transport(signer, requests)) as client:
        auth = host.EvalAuth(Settings(), SANDBOX, client)
        production = AuthClient(Settings(), client)
        with pytest.raises(HTTPException) as error:
            await production.verify(token(signer), role="buyer")
        assert error.value.status_code == 401
        for operation in (
            auth.verify(token(signer), role="merchant"),
            auth.verify(token(signer, permissions=["support:chat"]), role="buyer"),
            auth.login("unused", "synthetic-password", role="buyer"),
            auth.exchange(RequestIdentity("buyer", "unused"), "session", "merchant:read"),
        ):
            with pytest.raises(HTTPException) as error:
                await operation
            assert error.value.status_code == 403
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with pytest.raises(HTTPException) as error:
            await auth.verify(token(other), role="buyer")
        assert error.value.status_code == 401
        assert not any(r.url.path.endswith("/liveness") for r in requests)


@pytest.mark.asyncio
async def test_identity_outage_is_unavailable_without_response_body(signer):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(503, text="secret upstream response")
        )
    ) as client:
        with pytest.raises(HTTPException) as error:
            await host.EvalAuth(Settings(), SANDBOX, client).verify(token(signer), role="buyer")
        assert error.value.status_code == 503
        assert "secret" not in error.value.detail


@pytest.mark.asyncio
async def test_exchange_keeps_fixed_actor_scope_subject_and_sandbox(signer):
    calls = []

    def respond(request):
        calls.append(request)
        assert (
            base64.b64decode(request.headers["authorization"].split()[1]).decode()
            == "shopping-agent:synthetic-secret"
        )
        assert request.headers["x-user-authorization"] == "Bearer original-token"
        assert request.headers["x-eval-sandbox-id"] == SANDBOX
        assert json.loads(request.content) == {
            "sessionId": "session",
            "userSubject": "owner",
            "scope": "refund:create",
        }
        return httpx.Response(200, json={"accessToken": "delegated-token"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        auth = host.EvalAuth(Settings(shopping_service_secret="synthetic-secret"), SANDBOX, client)
        assert (
            await auth.exchange_shopping(
                RequestIdentity("owner", "original-token"), "session", "refund:create"
            )
            == "delegated-token"
        )
        with pytest.raises(ValueError):
            await auth.exchange_shopping(
                RequestIdentity("owner", "original-token"), "session", "refund:create merchant:read"
            )
        assert len(calls) == 1


def preferences():
    return {
        "userId": "evaluation-buyer",
        "displayName": None,
        "loyaltyTier": "NONE",
        "defaultLocation": None,
        "preferences": {},
    }


def cart():
    return {"version": 0, "currency": None, "subtotalMinor": 0, "checkoutReady": False, "items": []}


def policy():
    return {
        "policyId": "retail-policy-test",
        "title": "Refunds",
        "category": None,
        "content": "A paid order can be refunded by its owner.",
        "publicationVersion": 1,
        "publishedAt": "2026-09-07T00:00:00Z",
    }


def pending(request):
    arguments = json.loads(request.content)["arguments"]
    return {
        "pendingActionId": PENDING,
        "actionType": "REFUND_REQUEST",
        "userSubject": "evaluation-buyer",
        "supportSessionId": request.headers["x-shopping-session-id"],
        "traceId": request.headers["x-agent-trace-id"],
        "turnId": request.headers["x-agent-turn-id"],
        "requiredScope": "refund:create",
        "sandboxId": SANDBOX,
        "orderId": arguments["orderId"],
        "targetVersion": 2,
        "amountMinor": arguments["amountMinor"],
        "currency": arguments["currency"],
        "state": "PREPARED",
        "expiresAt": "2026-09-07T23:59:00Z",
        "replayed": False,
    }


def receipt():
    return {
        "receiptId": "receipt-one",
        "pendingActionId": PENDING,
        "actionType": "REFUND_REQUEST",
        "status": "REQUESTED",
        "orderId": ORDER,
        "refundId": "refund-one",
        "resourceVersion": 1,
        "amountMinor": 100,
        "currency": "CNY",
        "committedAt": "2026-09-07T00:00:00Z",
        "replayed": False,
    }


@pytest.mark.asyncio
async def test_five_read_mappings_and_original_refund_paths_preserve_business_dtos():
    calls = []

    def respond(request):
        calls.append(request)
        assert request.headers.get_list("x-eval-sandbox-id") == [SANDBOX]
        path = request.url.path
        if path.endswith("/preferences"):
            return httpx.Response(200, json=preferences())
        if path.endswith("/cart"):
            return httpx.Response(200, json=cart())
        if path.endswith("/policies"):
            assert request.headers["authorization"] == "Bearer direct"
            return httpx.Response(200, json=[policy()])
        if path.endswith("/orders"):
            return httpx.Response(200, json=[])
        if path.startswith("/internal/eval/shopping/orders/"):
            return httpx.Response(404)
        if path.endswith("/prepare"):
            return httpx.Response(200, json=pending(request))
        if path.endswith("/confirm"):
            return httpx.Response(200, json=receipt())
        raise AssertionError(path)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), headers={"X-Eval-Sandbox-Id": "ambient-other"}
    ) as client:
        buyer = host.EvalBuyerClient("http://commerce.invalid", SANDBOX, client)
        assert await buyer.orders("obo", "session") == []
        assert await buyer.order(ORDER, "obo", "session") is None
        assert (await buyer.preferences("obo", "session")).loyaltyTier == "NONE"
        assert (await buyer.cart("obo", "session")).version == 0
        assert (await buyer.policies("refund", "direct"))[0].publicationVersion == 1
        command = {
            "actionType": "REFUND_REQUEST",
            "arguments": {"orderId": ORDER, "amountMinor": 100, "currency": "CNY"},
        }
        value = await buyer.prepare_refund("obo", "session", "trace", "turn", command)
        assert value.amountMinor == 100
        assert (
            await buyer.confirm_refund(PENDING, "obo", "session", "trace", "turn")
        ).status == "REQUESTED"
        assert [r.url.path for r in calls] == [
            "/internal/eval/shopping/orders",
            "/internal/eval/shopping/orders/" + ORDER,
            "/internal/eval/shopping/preferences",
            "/internal/eval/shopping/cart",
            "/internal/eval/shopping/policies",
            "/internal/shopping/actions/prepare",
            f"/internal/shopping/actions/{PENDING}/confirm",
        ]
        assert json.loads(calls[-2].content) == command
        assert json.loads(calls[-1].content) == {}
        assert (
            calls[-2].headers["x-agent-trace-id"]
            == calls[-1].headers["x-agent-trace-id"]
            == "trace"
        )
        await buyer.close()
        assert not client.is_closed


class ModelStream(httpx.AsyncByteStream):
    def __init__(self, delta, finish):
        self.delta, self.finish = delta, finish

    async def __aiter__(self):
        for value in [
            {"id": "synthetic", "model": "fixture", "choices": [{"index": 0, "delta": self.delta}]},
            {
                "id": "synthetic",
                "model": "fixture",
                "choices": [{"index": 0, "delta": {}, "finish_reason": self.finish}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
        ]:
            yield ("data: " + json.dumps(value) + "\n\n").encode()
        yield b"data: [DONE]\n\n"


def tool_delta(call_id, name, arguments):
    return {
        "tool_calls": [
            {
                "index": 0,
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ]
    }


@pytest.mark.asyncio
async def test_real_buyer_loop_grounding_tools_memory_and_direct_confirmation(tmp_path, signer):
    settings = Settings(
        state_path=tmp_path / "trial.sqlite3", shopping_service_secret="synthetic-secret"
    )
    requests, model_calls = [], []

    def business(request):
        if request.url.path.endswith("/liveness"):
            return httpx.Response(204)
        if request.url.path == "/auth/token/exchange":
            assert request.headers["x-eval-sandbox-id"] == SANDBOX
            return httpx.Response(200, json={"accessToken": "evaluation-obo"})
        assert request.headers["x-eval-sandbox-id"] == SANDBOX
        if request.url.path == "/internal/eval/shopping/preferences":
            return httpx.Response(200, json=preferences())
        if request.url.path == "/internal/eval/shopping/cart":
            return httpx.Response(200, json=cart())
        if request.url.path == "/internal/eval/shopping/policies":
            return httpx.Response(200, json=[policy()])
        if request.url.path == "/internal/shopping/actions/prepare":
            UUID(request.headers["x-agent-trace-id"])
            UUID(request.headers["x-agent-turn-id"])
            return httpx.Response(200, json=pending(request))
        if request.url.path == f"/internal/shopping/actions/{PENDING}/confirm":
            return httpx.Response(200, json=receipt())
        raise AssertionError(request.url.path)

    def model(request):
        body = json.loads(request.content)
        model_calls.append(body)
        if not body.get("stream"):
            assert body["tools"][0]["function"]["name"] == "record_fact"
            return httpx.Response(
                200,
                json={
                    "id": "memory",
                    "model": "fixture",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "No new lasting preferences.",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2},
                },
            )
        count = sum(bool(call.get("stream")) for call in model_calls)
        if count == 1:
            assert body["tool_choice"]["function"]["name"] == "search_policies"
            delta, finish = (
                tool_delta("policies-1", "search_policies", {"query": "refund"}),
                "tool_calls",
            )
        elif count == 2:
            assert any(
                m.get("role") == "tool" and m["tool_call_id"] == "policies-1"
                for m in body["messages"]
            )
            delta, finish = (
                tool_delta(
                    "refund-1",
                    "prepare_refund",
                    {"order_id": ORDER, "amount_minor": 100, "currency": "CNY"},
                ),
                "tool_calls",
            )
        else:
            assert count == 3
            assert any(
                m.get("role") == "tool" and m["tool_call_id"] == "refund-1"
                for m in body["messages"]
            )
            delta, finish = {"content": "Please confirm the prepared refund."}, "stop"
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=ModelStream(delta, finish)
        )

    sdk = make_client(
        "https://model.invalid/v1",
        "synthetic-key",
        upstream_transport=httpx.MockTransport(model),
        before_request=lambda request: current_budget().consume(request),
        observe=lambda observation: current_budget().observations.append(observation),
    )
    provider = Provider(settings, client=sdk)
    async with httpx.AsyncClient(transport=transport(signer, requests, business)) as remote:
        auth = host.EvalAuth(settings, SANDBOX, remote)
        buyer = host.EvalBuyerClient(settings.commerce_url, SANDBOX, remote)
        app = host.create_evaluation_app(
            settings, SANDBOX, auth=auth, buyer_client=buyer, provider=provider
        )
        try:
            async with app.router.lifespan_context(app):
                resources = app.state.resources
                assert isinstance(resources["buyer_agent"], ShoppingAgent)
                assert isinstance(resources["buyer_backend"], CityBuddyStorefrontBackend)
                assert resources["buyer_agent"].memory.store is resources["memory"]
                names = {tool["name"] for tool in resources["buyer_agent"]._tools}
                assert {
                    "web_search",
                    "get_orders",
                    "prepare_refund",
                    "save_memory",
                    "search_policies",
                } <= names
                assert "confirm_refund" not in names
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://host.invalid"
                ) as client:
                    value = token(signer)
                    headers = {"Authorization": "Bearer " + value}
                    assert (
                        await client.post("/api/merchant/session", headers=headers)
                    ).status_code == 404
                    assert (
                        await client.post(
                            "/api/buyer/login",
                            json={"loginIdentifier": "unused", "password": "unused"},
                        )
                    ).status_code == 403
                    created = await client.post("/api/buyer/session", headers=headers)
                    assert created.status_code == 200
                    session = created.json()["session_id"]
                    headers["X-Session-Id"] = session
                    response = await client.post(
                        "/api/buyer/chat",
                        headers=headers,
                        json={
                            "message": f"Prepare a CNY 1.00 refund for order {ORDER}; what is the refund policy?"
                        },
                    )
                    assert response.status_code == 200
                    assert (
                        "refund_confirmation" in response.text and "turn_complete" in response.text
                    )
                    assert "no-transform" in response.headers["cache-control"]
                    assert not any(r.url.path.endswith("/confirm") for r in requests)
                    state = (await client.get("/api/buyer/session", headers=headers)).json()
                    assert state["status"] == "completed" and len(state["actions"]) == 1
                    assert len(model_calls) == 4
                    record = resources["store"].get(session, "evaluation-buyer", role="buyer")
                    assert record.items[-1]["memory_status"] == "unchanged"
                    assert record.items[-1]["provider_usage"]["model_calls"] == 4
                    assert (
                        await client.post(
                            f"/api/buyer/actions/{PENDING}/confirm", headers=headers, json={}
                        )
                    ).status_code == 200
                    writes = [r for r in requests if "/internal/shopping/actions/" in r.url.path]
                    assert len(writes) == 2
                    for name in ("x-agent-trace-id", "x-agent-turn-id", "x-shopping-session-id"):
                        assert writes[0].headers[name] == writes[1].headers[name]
            assert not sdk.is_closed() and not remote.is_closed
            with pytest.raises(sqlite3.ProgrammingError):
                resources["store"].db.execute("SELECT 1")
        finally:
            await provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("startup_failure", [False, True])
async def test_owned_defaults_close_even_when_factory_startup_fails(
    tmp_path, monkeypatch, startup_failure
):
    closed = []

    class Resource:
        def __init__(self, name):
            self.name, self.client, self.web_search = name, object(), None

        async def close(self):
            closed.append(self.name)

    monkeypatch.setattr(host, "EvalAuth", lambda *_: Resource("auth"))
    monkeypatch.setattr(host, "EvalBuyerClient", lambda *_: Resource("buyer"))
    monkeypatch.setattr(host, "Provider", lambda *_: Resource("provider"))
    if startup_failure:

        def fail(*args, **kwargs):
            raise RuntimeError("startup failed")

        monkeypatch.setattr("shopmate.provider.build_buyer_agent", fail)
    settings = Settings(state_path=tmp_path / "owned.sqlite3")
    app = host.create_evaluation_app(settings, SANDBOX)
    if startup_failure:
        with pytest.raises(RuntimeError, match="startup failed"):
            async with app.router.lifespan_context(app):
                raise AssertionError("Startup must not complete")
    else:
        async with app.router.lifespan_context(app):
            assert isinstance(app.state.resources["buyer_agent"], ShoppingAgent)
    assert closed == ["provider", "buyer", "auth"]
    with pytest.raises(sqlite3.ProgrammingError):
        app.state.resources["store"].db.execute("SELECT 1")


@pytest.mark.asyncio
async def test_existing_trial_state_is_never_opened_or_migrated(tmp_path):
    path = tmp_path / "existing.sqlite3"
    path.write_bytes(b"preserve existing state")
    app = host.create_evaluation_app(
        Settings(state_path=path), SANDBOX, auth=object(), buyer_client=object(), provider=object()
    )
    with pytest.raises(FileExistsError):
        async with app.router.lifespan_context(app):
            raise AssertionError("Existing state must not be opened")
    assert path.read_bytes() == b"preserve existing state"


def test_cli_accepts_only_settings_and_binds_ephemeral_loopback(tmp_path, monkeypatch):
    config = tmp_path / "settings.json"
    config.write_text(json.dumps({"state_path": str(tmp_path / "cli.sqlite3")}))
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: calls.append((app, kwargs)))
    monkeypatch.setattr(
        "sys.argv", ["shopmate_host", "--config", str(config), "--sandbox", SANDBOX, "--port", "0"]
    )
    host.main()
    assert calls[0][1] == {"host": "127.0.0.1", "port": 0, "access_log": False}
    assert not (tmp_path / "cli.sqlite3").exists()
    config.write_text(json.dumps({"unrecognized_key": "synthetic-secret"}))
    with pytest.raises(SystemExit, match="^Evaluation host configuration is invalid$"):
        host.main()
    assert len(calls) == 1
