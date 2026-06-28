from __future__ import annotations

import json
import os
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Dict

import httpx
from dotenv import load_dotenv


SCOPES = [
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.profile.readonly",
]
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def main() -> None:
    load_dotenv()
    client_id = os.getenv("GOOGLE_HEALTH_CLIENT_ID", "")
    client_secret = os.getenv("GOOGLE_HEALTH_CLIENT_SECRET", "")
    redirect_uri = os.getenv("GOOGLE_HEALTH_REDIRECT_URI", "http://127.0.0.1:8765/callback")
    token_path = Path(os.getenv("GOOGLE_HEALTH_TOKEN_PATH", "data/google_health_token.json"))

    if not client_id or not client_secret:
        raise SystemExit("请先在 .env 中配置 GOOGLE_HEALTH_CLIENT_ID 和 GOOGLE_HEALTH_CLIENT_SECRET。")

    parsed = urllib.parse.urlparse(redirect_uri)
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise SystemExit("首次授权脚本只支持本机回调，请使用 http://127.0.0.1:8765/callback。")

    code_holder: Dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "error" in query:
                code_holder["error"] = query["error"][0]
            if "code" in query:
                code_holder["code"] = query["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("Google Health 授权完成，可以关闭此页面。".encode("utf-8"))

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            return

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    auth_url = AUTH_URL + "?" + urllib.parse.urlencode(params)
    port = parsed.port or 8765
    server = HTTPServer((parsed.hostname or "127.0.0.1", port), CallbackHandler)
    server.timeout = 1

    print("正在打开浏览器授权 Google Health API...")
    print(auth_url)
    webbrowser.open(auth_url)

    deadline = time.time() + 180
    while time.time() < deadline and "code" not in code_holder and "error" not in code_holder:
        server.handle_request()

    if "error" in code_holder:
        raise SystemExit(f"Google OAuth 授权失败：{code_holder['error']}")
    if "code" not in code_holder:
        raise SystemExit("等待授权超时，请重新运行脚本。")

    token = exchange_code(code_holder["code"], client_id, client_secret, redirect_uri)
    token["expires_at"] = time.time() + int(token.get("expires_in", 3600))

    if not token_path.is_absolute():
        token_path = Path.cwd() / token_path
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(json.dumps(token, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Google Health token 已保存：{token_path}")


def exchange_code(code: str, client_id: str, client_secret: str, redirect_uri: str) -> Dict[str, object]:
    payload = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    response = httpx.post(TOKEN_URL, data=payload, timeout=20)
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    main()
