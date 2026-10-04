"""In-memory baseline. Implement the task routes in handle()."""

import hashlib
import hmac
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")
TEXT_MAX_BYTES = 65_536

# Routes shown in startup log; path-parameter routes use a representative path.
ROUTES = (
    ("GET", "/ping"),
    ("POST", "/echo"),
    ("POST", "/users"),
    ("POST", "/sessions"),
    ("DELETE", "/sessions/current"),
    ("DELETE", "/users/me"),
    ("GET", "/texts"),
    ("PUT", "/texts/{name}"),
    ("GET", "/texts/{name}"),
    ("DELETE", "/texts/{name}"),
)

# Exact-path lookup for 405 checking (path-parameter routes handled separately).
_EXACT_ROUTES: dict[str, str] = {
    "/ping": "GET",
    "/echo": "POST",
    "/users": "POST",
    "/sessions": "POST",
    "/sessions/current": "DELETE",
    "/users/me": "DELETE",
    "/texts": "GET",
}


def _text_name_from_path(path: str) -> str | None:
    """Return the text name if path matches /texts/{name}, else None."""
    if not path.startswith("/texts/"):
        return None
    name = path[len("/texts/") :]
    return name if name else None


def route_error(method: str, path: str) -> int | None:
    # Exact routes.
    if path in _EXACT_ROUTES:
        return None if method == _EXACT_ROUTES[path] else 405
    # Path-parameter routes under /texts/{name}.
    if path.startswith("/texts/") and path != "/texts/":
        # Any method among PUT/GET/DELETE is known; others are 405.
        if method in ("PUT", "GET", "DELETE"):
            return None
        return 405
    return 404


@dataclass
class User:
    salt: bytes
    digest: bytes
    token: str | None = None
    token_deadline: float = 0.0
    texts: dict[str, str] = field(default_factory=dict)


class Service:
    def __init__(self, token_ttl_seconds: int = 300) -> None:
        self.users: dict[str, User] = {}
        self.lock = threading.Lock()
        self.token_ttl_seconds = token_ttl_seconds

    def handle(
        self, method: str, path: str, body: Any, authorization: str
    ) -> tuple[int, dict[str, Any]]:
        if status := route_error(method, path):
            return status, {"message": "Not found" if status == 404 else "Method not allowed"}
        if method == "GET" and path == "/ping":
            return 200, {"data": "pong"}
        if method == "POST" and path == "/echo":
            if not isinstance(body, dict) or set(body) != {"text"}:
                return 400, {"message": "Expected text field"}
            text = body["text"]
            if not isinstance(text, str):
                return 400, {"message": "text must be a string"}
            try:
                encoded = text.encode("utf-8")
            except UnicodeError:
                return 400, {"message": "text must be valid Unicode"}
            if len(encoded) > TEXT_MAX_BYTES:
                return 413, {"message": "text too large"}
            return 200, {"data": text}
        if path in ("/users", "/sessions") and method == "POST":
            if not isinstance(body, dict) or set(body) != {"username", "password"}:
                return 400, {"message": "Expected username and password"}
            name, password = body["username"], body["password"]
            if (
                not isinstance(name, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", name)
                or not isinstance(password, str)
                or not 8 <= len(password) <= 128
            ):
                return 400, {"message": "Invalid username or password length"}
            try:
                password.encode("utf-8")
            except UnicodeError:
                return 400, {"message": "Password must be valid Unicode"}
            # Hashing is outside the state lock; commit/check against current state under lock.
            if path == "/users":
                salt = secrets.token_bytes(16)
                digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
                with self.lock:
                    if name in self.users:
                        return 409, {"message": "Username exists"}
                    self.users[name] = User(salt, digest)
                return 201, {"data": {"username": name}}
            with self.lock:
                user = self.users.get(name)
                if user is None:
                    return 401, {"message": "Invalid username or password"}
                salt, expected = user.salt, user.digest
            digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
            with self.lock:
                if self.users.get(name) is not user or not hmac.compare_digest(digest, expected):
                    return 401, {"message": "Invalid username or password"}
                user.token = secrets.token_urlsafe(32)
                user.token_deadline = time.monotonic() + self.token_ttl_seconds
                return 200, {"data": {"token": user.token, "expires_in": self.token_ttl_seconds}}
        protected = path in ("/texts", "/sessions/current", "/users/me") or (
            path.startswith("/texts/") and path != "/texts/"
        )
        if protected:
            token = (
                authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
            )
            with self.lock:
                user = next((u for u in self.users.values() if token and u.token == token), None)
                if user is None or time.monotonic() >= user.token_deadline:
                    return 401, {"message": "Login required"}
                if path == "/sessions/current" and method == "DELETE":
                    user.token = None
                    return 200, {"data": None}
                if path == "/users/me" and method == "DELETE":
                    # Remove the user and all associated data.
                    for name in list(self.users):
                        if self.users[name] is user:
                            del self.users[name]
                            break
                    return 200, {"data": None}
                if path == "/texts" and method == "GET":
                    return 200, {"data": sorted(user.texts)}
                # Text item routes: /texts/{name}
                name = _text_name_from_path(path)
                if name is not None:
                    if not NAME_PATTERN.fullmatch(name):
                        return 400, {"message": "Invalid text name"}
                    if method == "PUT":
                        if not isinstance(body, dict) or set(body) != {"text"}:
                            return 400, {"message": "Expected text field"}
                        text = body["text"]
                        if not isinstance(text, str):
                            return 400, {"message": "text must be a string"}
                        try:
                            encoded = text.encode("utf-8")
                        except UnicodeError:
                            return 400, {"message": "text must be valid Unicode"}
                        if len(encoded) > TEXT_MAX_BYTES:
                            return 413, {"message": "text too large"}
                        user.texts[name] = text
                        return 200, {"data": None}
                    if method == "GET":
                        if name not in user.texts:
                            return 404, {"message": "Text not found"}
                        return 200, {"data": user.texts[name]}
                    if method == "DELETE":
                        if name not in user.texts:
                            return 404, {"message": "Text not found"}
                        del user.texts[name]
                        return 200, {"data": None}
        return 404, {"message": "Not found"}
