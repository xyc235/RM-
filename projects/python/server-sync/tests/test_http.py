from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from text_service.server import create_app
from text_service.service import Service


@pytest.fixture
def client() -> Generator[TestClient]:
    with TestClient(create_app()) as client:
        yield client


def test_app_uses_supplied_service() -> None:
    service = Service()
    account = {"username": "alice", "password": "password1"}
    assert service.handle("POST", "/users", account, "")[0] == 201
    with TestClient(create_app(service)) as configured, TestClient(create_app()) as fresh:
        assert configured.post("/sessions", json=account).status_code == 200
        assert fresh.post("/sessions", json=account).status_code == 401


def test_http_routes(client: TestClient) -> None:
    assert client.get("/ping").status_code == 200
    response = client.post("/users", json={"username": "alice", "password": "password1"})
    assert response.status_code == 201
    response = client.post("/sessions", json={"username": "alice", "password": "password1"})
    token = response.json()["data"]["token"]
    assert (client.get("/texts", headers={"Authorization": f"Bearer {token}"})).status_code == 200
    assert client.get("/texts").status_code == 401
    assert (
        client.post("/users", content=b"not JSON", headers={"Content-Type": "application/json"})
    ).status_code == 400
    assert (
        client.post("/users", content=b"x" * 524289, headers={"Content-Type": "application/json"})
    ).status_code == 413


@pytest.mark.parametrize("body", [b"not JSON", b"\xff", b"NaN"])
def test_invalid_json(client: TestClient, body: bytes) -> None:
    assert client.post("/users", content=body).status_code == 400


def test_body_limit_and_routing(client: TestClient) -> None:
    exact = b"{}" + b" " * (524288 - 2)
    assert client.post("/users", content=exact).status_code == 400
    assert client.post("/users", content=exact + b" ").status_code == 413
    assert client.get("/missing").status_code == 404
    assert client.patch("/echo").status_code == 405
    assert client.patch("/ping").status_code == 405
    assert client.get("/ping?test=1").json() == {"data": "pong"}


@pytest.mark.parametrize("path", ["/ping", "/users", "/sessions", "/sessions/current", "/texts"])
def test_wrong_method_precedes_authentication(client: TestClient, path: str) -> None:
    assert client.patch(path).status_code == 405


def test_echo(client: TestClient) -> None:
    for text in ("你好\nRM", "", "line1\nline2\n"):
        response = client.post("/echo", json={"text": text})
        assert response.status_code == 200
        assert response.json() == {"data": text}


def test_echo_validation(client: TestClient) -> None:
    assert client.post("/echo", json={}).status_code == 400
    assert client.post("/echo", json={"text": 42}).status_code == 400
    assert client.post("/echo", json={"text": "a", "extra": 1}).status_code == 400
    assert client.post("/echo", content=b"not JSON").status_code == 400


def test_echo_too_large(client: TestClient) -> None:
    big = "x" * (65536 + 1)
    assert client.post("/echo", json={"text": big}).status_code == 413


def _register_and_login(client: TestClient, username: str = "alice") -> str:
    client.post("/users", json={"username": username, "password": "password1"})
    response = client.post("/sessions", json={"username": username, "password": "password1"})
    return response.json()["data"]["token"]


def test_text_crud(client: TestClient) -> None:
    token = _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    # Upload
    assert client.put("/texts/note", json={"text": "hello"}, headers=headers).status_code == 200
    # Read back
    response = client.get("/texts/note", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"data": "hello"}
    # List
    assert client.get("/texts", headers=headers).json() == {"data": ["note"]}
    # Overwrite
    client.put("/texts/note", json={"text": "world"}, headers=headers)
    assert client.get("/texts/note", headers=headers).json() == {"data": "world"}
    # Delete
    assert client.delete("/texts/note", headers=headers).status_code == 200
    assert client.get("/texts", headers=headers).json() == {"data": []}
    # 404 after delete
    assert client.get("/texts/note", headers=headers).status_code == 404
    assert client.delete("/texts/note", headers=headers).status_code == 404


def test_text_name_validation(client: TestClient) -> None:
    token = _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.put("/texts/bad name", json={"text": "x"}, headers=headers).status_code == 400
    assert client.put("/texts/a" * 65, json={"text": "x"}, headers=headers).status_code == 400


def test_text_requires_auth(client: TestClient) -> None:
    assert client.put("/texts/note", json={"text": "x"}).status_code == 401
    assert client.get("/texts/note").status_code == 401
    assert client.delete("/texts/note").status_code == 401


def test_text_too_large(client: TestClient) -> None:
    token = _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    big = "x" * (65536 + 1)
    assert client.put("/texts/note", json={"text": big}, headers=headers).status_code == 413


def test_text_isolation(client: TestClient) -> None:
    token_a = _register_and_login(client, "alice")
    token_b = _register_and_login(client, "bob")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}
    client.put("/texts/note", json={"text": "alice's note"}, headers=headers_a)
    client.put("/texts/note", json={"text": "bob's note"}, headers=headers_b)
    # Each user sees only their own text.
    assert client.get("/texts/note", headers=headers_a).json() == {"data": "alice's note"}
    assert client.get("/texts/note", headers=headers_b).json() == {"data": "bob's note"}
    assert client.get("/texts", headers=headers_a).json() == {"data": ["note"]}
    # Deleting alice's note does not affect bob.
    client.delete("/texts/note", headers=headers_a)
    assert client.get("/texts/note", headers=headers_b).status_code == 200


def test_delete_user(client: TestClient) -> None:
    token = _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    client.put("/texts/note", json={"text": "hello"}, headers=headers)
    # Delete account
    assert client.delete("/users/me", headers=headers).status_code == 200
    # Old token is invalid
    assert client.get("/texts", headers=headers).status_code == 401
    # Re-register same name, no old data
    token2 = _register_and_login(client)
    headers2 = {"Authorization": f"Bearer {token2}"}
    assert client.get("/texts", headers=headers2).json() == {"data": []}


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
def test_method_not_allowed(client: TestClient, method: str, path: str) -> None:
    assert client.request(method, path).status_code == 405


def test_unknown_path_404(client: TestClient) -> None:
    assert client.get("/unknown").status_code == 404
    assert client.get("/texts/").status_code == 404


def test_empty_text_roundtrip(client: TestClient) -> None:
    token = _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.put("/texts/note", json={"text": ""}, headers=headers).status_code == 200
    assert client.get("/texts/note", headers=headers).json() == {"data": ""}


def test_text_list_sorted(client: TestClient) -> None:
    token = _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    for name in ("zebra", "apple", "mango"):
        client.put(f"/texts/{name}", json={"text": "x"}, headers=headers)
    assert client.get("/texts", headers=headers).json() == {"data": ["apple", "mango", "zebra"]}
