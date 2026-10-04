from text_service.service import Service


def test_account_lifecycle() -> None:
    service = Service()
    account = {"username": "alice", "password": "password1"}
    assert service.handle("GET", "/ping", None, "") == (200, {"data": "pong"})
    assert service.handle("POST", "/users", account, "")[0] == 201
    assert service.handle("POST", "/users", account, "")[0] == 409
    assert service.handle("POST", "/sessions", {**account, "password": "incorrect"}, "")[0] == 401
    session = service.handle("POST", "/sessions", account, "")[1]["data"]
    token = session["token"]
    assert session["expires_in"] == 300
    next_session = service.handle("POST", "/sessions", account, "")[1]["data"]
    next_token = next_session["token"]
    assert token != next_token
    assert service.handle("GET", "/texts", None, f"Bearer {token}")[0] == 401
    assert service.handle("GET", "/texts", None, f"Bearer {next_token}") == (200, {"data": []})
    assert service.handle("DELETE", "/sessions/current", None, f"Bearer {next_token}")[0] == 200
    assert service.handle("GET", "/texts", None, f"Bearer {next_token}")[0] == 401


def test_validation() -> None:
    service = Service()
    for body in (
        None,
        [],
        {},
        {"username": True, "password": "password1"},
        {"username": "a/b", "password": "password1"},
    ):
        assert service.handle("POST", "/users", body, "")[0] == 400


def test_concurrent_registration() -> None:
    from concurrent.futures import ThreadPoolExecutor

    service = Service()
    body = {"username": "alice", "password": "password1"}
    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(lambda _: service.handle("POST", "/users", body, "")[0], range(4)))
    assert sorted(statuses) == [201, 409, 409, 409]


def test_token_expiry() -> None:
    import time

    service = Service(token_ttl_seconds=1)
    account = {"username": "alice", "password": "password1"}
    service.handle("POST", "/users", account, "")
    session = service.handle("POST", "/sessions", account, "")[1]["data"]
    token = session["token"]
    assert session["expires_in"] == 1
    # Token works immediately.
    assert service.handle("GET", "/texts", None, f"Bearer {token}")[0] == 200
    # Wait for expiry.
    time.sleep(1.1)
    assert service.handle("GET", "/texts", None, f"Bearer {token}")[0] == 401
    # Re-login produces a new working token.
    session2 = service.handle("POST", "/sessions", account, "")[1]["data"]
    token2 = session2["token"]
    assert token2 != token
    assert service.handle("GET", "/texts", None, f"Bearer {token2}")[0] == 200


def test_token_ttl_config() -> None:
    service = Service(token_ttl_seconds=60)
    account = {"username": "alice", "password": "password1"}
    service.handle("POST", "/users", account, "")
    session = service.handle("POST", "/sessions", account, "")[1]["data"]
    assert session["expires_in"] == 60


def test_concurrent_text_operations() -> None:
    """Concurrent uploads of distinct names all land in the user's texts."""
    from concurrent.futures import ThreadPoolExecutor

    service = Service()
    account = {"username": "alice", "password": "password1"}
    service.handle("POST", "/users", account, "")
    token = service.handle("POST", "/sessions", account, "")[1]["data"]["token"]
    auth = f"Bearer {token}"

    names = [f"note{i}" for i in range(20)]

    def upload(name: str) -> int:
        return service.handle("PUT", f"/texts/{name}", {"text": name}, auth)[0]

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(upload, names))
    assert statuses == [200] * 20
    listed = service.handle("GET", "/texts", None, auth)[1]["data"]
    assert listed == sorted(names)


def test_concurrent_login_replaces_token() -> None:
    """Concurrent logins leave exactly one valid token for the user."""
    from concurrent.futures import ThreadPoolExecutor

    service = Service()
    account = {"username": "alice", "password": "password1"}
    service.handle("POST", "/users", account, "")

    with ThreadPoolExecutor(max_workers=4) as pool:
        sessions = list(
            pool.map(
                lambda _: service.handle("POST", "/sessions", account, "")[1]["data"], range(4)
            )
        )
    tokens = {s["token"] for s in sessions}
    # At most one of the issued tokens should still be valid.
    valid = [t for t in tokens if service.handle("GET", "/texts", None, f"Bearer {t}")[0] == 200]
    assert len(valid) == 1


def test_delete_user_then_reregister_no_old_data() -> None:
    service = Service()
    account = {"username": "alice", "password": "password1"}
    service.handle("POST", "/users", account, "")
    token = service.handle("POST", "/sessions", account, "")[1]["data"]["token"]
    auth = f"Bearer {token}"
    service.handle("PUT", "/texts/note", {"text": "hello"}, auth)
    # Delete account.
    assert service.handle("DELETE", "/users/me", None, auth)[0] == 200
    # Old token invalid.
    assert service.handle("GET", "/texts", None, auth)[0] == 401
    # Re-register same name.
    assert service.handle("POST", "/users", account, "")[0] == 201
    token2 = service.handle("POST", "/sessions", account, "")[1]["data"]["token"]
    assert service.handle("GET", "/texts", None, f"Bearer {token2}") == (200, {"data": []})


def test_unknown_routes_and_methods() -> None:
    service = Service()
    assert service.handle("GET", "/missing", None, "")[0] == 404
    assert service.handle("POST", "/ping", {}, "")[0] == 405
    assert service.handle("PATCH", "/texts", None, "")[0] == 405
    assert service.handle("PATCH", "/texts/note", None, "")[0] == 405
