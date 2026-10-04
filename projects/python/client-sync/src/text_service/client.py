import argparse
import getpass
from typing import Any

import httpx

END_MARKER = "---END---"


def exchange(
    client: httpx.Client, method: str, path: str, token: str = "", body: object = None
) -> tuple[int, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = client.request(method, path, json=body, headers=headers)
    try:
        result = response.json()
    except ValueError:
        result = {"message": response.text}
    return response.status_code, result


def read_multiline(prompt: str) -> str:
    """Read possibly multi-line text until a line with only END_MARKER is entered.

    The marker line itself is not part of the text, so a body line equal to the
    marker can still be expressed by splitting it across two lines or escaping.
    An empty line right before the marker is preserved as a trailing newline.
    """
    print(prompt)
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == END_MARKER:
            break
        lines.append(line)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:7878")
    args = parser.parse_args()
    token = ""
    with httpx.Client(
        base_url=args.url, timeout=12, follow_redirects=False, trust_env=False
    ) as client:
        try:
            while True:
                command = input(
                    "ping / register / login / logout / list / echo / "
                    "delete-user / put / get / delete / q > "
                ).strip()
                body = None
                if command == "q":
                    break
                if command in ("register", "login"):
                    body = {
                        "username": input("username: "),
                        "password": getpass.getpass("password: "),
                    }
                    method, path = "POST", "/users" if command == "register" else "/sessions"
                elif command in ("ping", "logout", "list"):
                    method, path = {
                        "ping": ("GET", "/ping"),
                        "logout": ("DELETE", "/sessions/current"),
                        "list": ("GET", "/texts"),
                    }[command]
                elif command == "echo":
                    text = read_multiline("Enter text (end with a line '---END---'):")
                    body = {"text": text}
                    method, path = "POST", "/echo"
                elif command == "put":
                    name = input("text name: ").strip()
                    text = read_multiline("Enter text (end with a line '---END---'):")
                    body = {"text": text}
                    method, path = "PUT", f"/texts/{name}"
                elif command == "get":
                    name = input("text name: ").strip()
                    method, path = "GET", f"/texts/{name}"
                elif command == "delete":
                    name = input("text name: ").strip()
                    method, path = "DELETE", f"/texts/{name}"
                elif command == "delete-user":
                    method, path = "DELETE", "/users/me"
                else:
                    print("Unknown command.")
                    continue
                try:
                    status, result = exchange(client, method, path, token, body)
                    print(status, result)
                    if command == "login" and status == 200:
                        token = result["data"]["token"]
                    if status == 401:
                        print("Please log in again.")
                    if status == 401 or (command in ("logout", "delete-user") and status == 200):
                        token = ""
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    print(f"Request failed: {exc}")
        except (EOFError, KeyboardInterrupt):
            print()
