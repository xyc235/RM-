import httpx
import pytest

from text_service.client import exchange, read_multiline


def test_request() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/texts"
        assert request.headers["Authorization"] == "Bearer example"
        return httpx.Response(200, json={"data": []})

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(respond)
    ) as client:
        assert exchange(client, "GET", "/texts", "example") == (200, {"data": []})


def test_echo() -> None:
    captured: dict[str, object] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        import json

        payload = json.loads(request.content)
        captured["text"] = payload["text"]
        return httpx.Response(200, json={"data": payload["text"]})

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(respond)
    ) as client:
        for text in ("你好\nRM", "", "line1\nline2\n", "---END---"):
            status, result = exchange(client, "POST", "/echo", "", {"text": text})
            assert status == 200
            assert result["data"] == text
            assert captured["text"] == text


def test_read_multiline(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate user typing two lines then the end marker.
    inputs = iter(["hello", "world", "---END---"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    assert read_multiline("prompt") == "hello\nworld"


def test_read_multiline_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    # Immediately end marker -> empty string.
    inputs = iter(["---END---"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    assert read_multiline("prompt") == ""


def test_read_multiline_trailing_newline(monkeypatch: pytest.MonkeyPatch) -> None:
    # A blank line before the marker is preserved as a trailing newline.
    inputs = iter(["text", "", "---END---"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    assert read_multiline("prompt") == "text\n"


def test_put_text() -> None:
    captured: dict[str, object] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        import json

        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["auth"] = request.headers.get("Authorization", "")
        payload = json.loads(request.content)
        captured["text"] = payload["text"]
        return httpx.Response(200, json={"data": None})

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(respond)
    ) as client:
        status, result = exchange(client, "PUT", "/texts/note", "tok", {"text": "hello"})
        assert status == 200
        assert result == {"data": None}
        assert captured["method"] == "PUT"
        assert captured["path"] == "/texts/note"
        assert captured["auth"] == "Bearer tok"
        assert captured["text"] == "hello"


def test_get_text() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/texts/note"
        return httpx.Response(200, json={"data": "你好\nRM"})

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(respond)
    ) as client:
        status, result = exchange(client, "GET", "/texts/note", "tok")
        assert status == 200
        assert result["data"] == "你好\nRM"


def test_delete_text() -> None:
    captured: dict[str, object] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        return httpx.Response(200, json={"data": None})

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(respond)
    ) as client:
        status, result = exchange(client, "DELETE", "/texts/note", "tok")
        assert status == 200
        assert result == {"data": None}
        assert captured["method"] == "DELETE"
        assert captured["path"] == "/texts/note"


def test_delete_user() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path == "/users/me"
        return httpx.Response(200, json={"data": None})

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(respond)
    ) as client:
        status, result = exchange(client, "DELETE", "/users/me", "tok")
        assert status == 200
        assert result == {"data": None}


def test_401_clears_token_logic() -> None:
    # Verify that a 401 response is properly returned for the caller to act on.
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Login required"})

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(respond)
    ) as client:
        status, result = exchange(client, "GET", "/texts", "stale-token")
        assert status == 401
        assert "message" in result


def test_non_json_error_body() -> None:
    # When the response body is not JSON, the status code is still available.
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"internal error", headers={})

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(respond)
    ) as client:
        status, result = exchange(client, "GET", "/ping")
        assert status == 500
        assert "message" in result
