"""Higgsfield MCP client.

The Higgsfield website plan and the Higgsfield API are two different products:

- the **API** runs on its own dollar balance, and
- the **MCP server** (and the CLI) spend the **plan credits** of the account.

The factory therefore talks to the MCP server directly, so a reel can be made
from plan credits instead of API dollars.

The MCP server uses OAuth 2.1 with the device authorization grant, so a
headless worker can authorise itself once:

1. the factory registers a client and asks for a device code,
2. the owner opens the printed URL and confirms the code in a browser,
3. the factory exchanges the code for an access token and a refresh token and
   stores both, so later jobs need no browser at all.
"""
import json
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

MCP_URL = "https://mcp.higgsfield.ai/mcp"
OAUTH_BASE = "https://mcp.higgsfield.ai"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
SCOPES = "openid email offline_access"


@dataclass(frozen=True)
class McpTool:
    name: str
    description: str


class McpError(RuntimeError):
    """The MCP server refused a request."""


class HiggsfieldMcpClient:
    """A minimal MCP client for the Higgsfield server."""

    def __init__(
        self,
        access_token: str | None = None,
        *,
        mcp_url: str = MCP_URL,
        timeout: float = 120.0,
    ) -> None:
        self.access_token = access_token
        self.mcp_url = mcp_url
        self.timeout = timeout
        self._request_id = 0

    # ── OAuth ─────────────────────────────────────────────────────────────
    def register_client(self, client_name: str = "Hindi Content Factory") -> dict:
        response = httpx.post(
            f"{OAUTH_BASE}/oauth2/register",
            json={
                "client_name": client_name,
                "redirect_uris": [],
                "token_endpoint_auth_method": "none",
                "grant_types": [DEVICE_GRANT, "refresh_token"],
                "response_types": ["code"],
                "scope": SCOPES,
            },
            timeout=60,
        )
        response.raise_for_status()
        return response.json()

    def start_device_flow(self, client_id: str) -> dict:
        """Ask for a device code the owner confirms in a browser."""
        response = httpx.post(
            f"{OAUTH_BASE}/oauth2/token",
            data={
                "grant_type": DEVICE_GRANT,
                "client_id": client_id,
                "scope": SCOPES,
            },
            timeout=60,
        )
        if response.status_code >= 400:
            raise McpError(
                f"The device flow was refused: HTTP {response.status_code} {response.text[:200]}"
            )
        return response.json()

    def wait_for_device_token(
        self,
        client_id: str,
        device_code: str,
        interval: int = 5,
        timeout: int = 600,
    ) -> dict:
        """Poll the token endpoint until the owner confirms the code."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            response = httpx.post(
                f"{OAUTH_BASE}/oauth2/token",
                data={
                    "grant_type": DEVICE_GRANT,
                    "client_id": client_id,
                    "device_code": device_code,
                },
                timeout=60,
            )
            if response.status_code == 200:
                return response.json()
            payload: dict = {}
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            error = str(payload.get("error") or "")
            if error not in {"authorization_pending", "slow_down"}:
                raise McpError(
                    f"The device authorisation failed: {error or response.status_code} "
                    f"{str(payload.get('error_description') or '')[:200]}"
                )
            time.sleep(interval)
        raise McpError("The device authorisation was not confirmed in time.")

    def refresh(self, client_id: str, refresh_token: str) -> dict:
        response = httpx.post(
            f"{OAUTH_BASE}/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": refresh_token,
            },
            timeout=60,
        )
        response.raise_for_status()
        return response.json()

    # ── MCP ───────────────────────────────────────────────────────────────
    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params or {},
        }
        response = httpx.post(
            self.mcp_url,
            headers=self._headers(),
            json=payload,
            timeout=self.timeout,
        )
        if response.status_code in {401, 403}:
            raise McpError("The Higgsfield MCP session is not authorised.")
        if response.status_code >= 400:
            raise McpError(
                f"Higgsfield MCP refused {method}: HTTP {response.status_code} {response.text[:200]}"
            )
        body = response.text
        if body.startswith("event:") or "\ndata:" in body:
            for line in body.splitlines():
                if line.startswith("data:"):
                    body = line[5:].strip()
                    break
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise McpError("Higgsfield MCP returned an unreadable answer.") from exc
        if isinstance(parsed, dict) and parsed.get("error"):
            raise McpError(f"Higgsfield MCP error: {str(parsed['error'])[:300]}")
        return parsed.get("result") or {}

    def initialize(self) -> dict:
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "hindi-content-factory", "version": "1.0"},
            },
        )
        try:
            self._rpc("notifications/initialized", {})
        except McpError:
            pass
        return result

    def list_tools(self) -> list[McpTool]:
        result = self.list_tools_raw()
        return [
            McpTool(
                name=str(item.get("name") or ""),
                description=str(item.get("description") or "")[:300],
            )
            for item in result
            if isinstance(item, dict)
        ]

    def list_tools_raw(self) -> list:
        result = self._rpc("tools/list", {})
        tools = result.get("tools")
        return tools if isinstance(tools, list) else []

    def call_tool(self, name: str, arguments: dict) -> str:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        parts: list[str] = []
        for item in result.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        text = "\n".join(parts).strip()
        if not text:
            text = json.dumps(result)[:2000]
        return text


def load_session(path: Path) -> dict:
    """Read the stored MCP session (client id, access token, refresh token)."""
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_session(path: Path, session: dict) -> None:
    """Store the MCP session for later jobs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
