from collections.abc import AsyncGenerator

import pytest
from httpx2 import ASGITransport, AsyncClient

from text_service.server import create_app

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient]:
    app = create_app()
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
    ):
        yield client


async def test_http_routes(client: AsyncClient) -> None:
    assert (await client.get("/ping")).status_code == 200
    response = await client.post("/users", json={"username": "alice", "password": "password1"})
    assert response.status_code == 201
    response = await client.post("/sessions", json={"username": "alice", "password": "password1"})
    token = response.json()["data"]["token"]
    assert (
        await client.get("/texts", headers={"Authorization": f"Bearer {token}"})
    ).status_code == 200
    assert (await client.get("/texts")).status_code == 401
    assert (
        await client.post(
            "/users", content=b"not JSON", headers={"Content-Type": "application/json"}
        )
    ).status_code == 400
    assert (
        await client.post(
            "/users", content=b"x" * 524289, headers={"Content-Type": "application/json"}
        )
    ).status_code == 413


@pytest.mark.parametrize("body", [b"not JSON", b"\xff", b"NaN"])
async def test_invalid_json(client: AsyncClient, body: bytes) -> None:
    assert (await client.post("/users", content=body)).status_code == 400


async def test_body_limit_and_routing(client: AsyncClient) -> None:
    exact = b"{}" + b" " * (524288 - 2)
    assert (await client.post("/users", content=exact)).status_code == 400
    assert (await client.post("/users", content=exact + b" ")).status_code == 413
    assert (await client.get("/missing")).status_code == 404
    assert (await client.patch("/echo")).status_code == 405
    assert (await client.patch("/ping")).status_code == 405
    assert (await client.get("/ping?test=1")).json() == {"data": "pong"}


@pytest.mark.parametrize("path", ["/ping", "/users", "/sessions", "/sessions/current", "/texts"])
async def test_wrong_method_precedes_authentication(client: AsyncClient, path: str) -> None:
    assert (await client.patch(path)).status_code == 405


async def test_echo(client: AsyncClient) -> None:
    for text in ("你好\nRM", "", "line1\nline2\n"):
        response = await client.post("/echo", json={"text": text})
        assert response.status_code == 200
        assert response.json() == {"data": text}


async def test_echo_validation(client: AsyncClient) -> None:
    assert (await client.post("/echo", json={})).status_code == 400
    assert (await client.post("/echo", json={"text": 42})).status_code == 400
    assert (await client.post("/echo", json={"text": "a", "extra": 1})).status_code == 400
    assert (await client.post("/echo", content=b"not JSON")).status_code == 400


async def test_echo_too_large(client: AsyncClient) -> None:
    big = "x" * (65536 + 1)
    assert (await client.post("/echo", json={"text": big})).status_code == 413


async def _register_and_login(client: AsyncClient, username: str = "alice") -> str:
    await client.post("/users", json={"username": username, "password": "password1"})
    response = await client.post("/sessions", json={"username": username, "password": "password1"})
    return response.json()["data"]["token"]


async def test_text_crud(client: AsyncClient) -> None:
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    assert (
        await client.put("/texts/note", json={"text": "hello"}, headers=headers)
    ).status_code == 200
    response = await client.get("/texts/note", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"data": "hello"}
    assert (await client.get("/texts", headers=headers)).json() == {"data": ["note"]}
    await client.put("/texts/note", json={"text": "world"}, headers=headers)
    assert (await client.get("/texts/note", headers=headers)).json() == {"data": "world"}
    assert (await client.delete("/texts/note", headers=headers)).status_code == 200
    assert (await client.get("/texts", headers=headers)).json() == {"data": []}
    assert (await client.get("/texts/note", headers=headers)).status_code == 404
    assert (await client.delete("/texts/note", headers=headers)).status_code == 404


async def test_text_name_validation(client: AsyncClient) -> None:
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    assert (
        await client.put("/texts/bad name", json={"text": "x"}, headers=headers)
    ).status_code == 400
    assert (
        await client.put("/texts/" + "a" * 65, json={"text": "x"}, headers=headers)
    ).status_code == 400


async def test_text_requires_auth(client: AsyncClient) -> None:
    assert (await client.put("/texts/note", json={"text": "x"})).status_code == 401
    assert (await client.get("/texts/note")).status_code == 401
    assert (await client.delete("/texts/note")).status_code == 401


async def test_text_too_large(client: AsyncClient) -> None:
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    big = "x" * (65536 + 1)
    assert (await client.put("/texts/note", json={"text": big}, headers=headers)).status_code == 413


async def test_text_isolation(client: AsyncClient) -> None:
    token_a = await _register_and_login(client, "alice")
    token_b = await _register_and_login(client, "bob")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}
    await client.put("/texts/note", json={"text": "alice's note"}, headers=headers_a)
    await client.put("/texts/note", json={"text": "bob's note"}, headers=headers_b)
    assert (await client.get("/texts/note", headers=headers_a)).json() == {"data": "alice's note"}
    assert (await client.get("/texts/note", headers=headers_b)).json() == {"data": "bob's note"}
    assert (await client.get("/texts", headers=headers_a)).json() == {"data": ["note"]}
    await client.delete("/texts/note", headers=headers_a)
    assert (await client.get("/texts/note", headers=headers_b)).status_code == 200


async def test_delete_user(client: AsyncClient) -> None:
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    await client.put("/texts/note", json={"text": "hello"}, headers=headers)
    assert (await client.delete("/users/me", headers=headers)).status_code == 200
    assert (await client.get("/texts", headers=headers)).status_code == 401
    token2 = await _register_and_login(client)
    headers2 = {"Authorization": f"Bearer {token2}"}
    assert (await client.get("/texts", headers=headers2)).json() == {"data": []}


async def test_session_expires_in(client: AsyncClient) -> None:
    await client.post("/users", json={"username": "alice", "password": "password1"})
    response = await client.post("/sessions", json={"username": "alice", "password": "password1"})
    assert response.json()["data"]["expires_in"] == 300


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PATCH", "/users/me"),
        ("PATCH", "/texts/note"),
        ("POST", "/texts/note"),
        ("DELETE", "/echo"),
        ("PUT", "/ping"),
    ],
)
async def test_method_not_allowed(client: AsyncClient, method: str, path: str) -> None:
    assert (await client.request(method, path)).status_code == 405


async def test_unknown_path_404(client: AsyncClient) -> None:
    assert (await client.get("/unknown")).status_code == 404
    assert (await client.get("/texts/")).status_code == 404


async def test_empty_text_roundtrip(client: AsyncClient) -> None:
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.put("/texts/note", json={"text": ""}, headers=headers)).status_code == 200
    assert (await client.get("/texts/note", headers=headers)).json() == {"data": ""}


async def test_text_list_sorted(client: AsyncClient) -> None:
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    for name in ("zebra", "apple", "mango"):
        await client.put(f"/texts/{name}", json={"text": "x"}, headers=headers)
    assert (await client.get("/texts", headers=headers)).json() == {
        "data": ["apple", "mango", "zebra"]
    }
