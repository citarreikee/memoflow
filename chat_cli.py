"""Terminal chat client for the Memoflow conversation backend."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, Optional

import httpx


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chat with the Memoflow backend from your terminal.")
    parser.add_argument("--base-url", default="http://localhost:3001", help="Backend base URL.")
    parser.add_argument("--model", default="deepseek-chat", help="Model name to use.")
    parser.add_argument("--session-key", default="cli", help="Stable session key for this terminal chat.")
    parser.add_argument("--no-tools", action="store_true", help="Disable ReAct tool calling for chat turns.")
    return parser.parse_args()


def _read_sse_payload(line: str) -> Optional[Dict[str, Any]]:
    if not line.startswith("data: "):
        return None
    try:
        payload = json.loads(line[6:])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _stream_turn(
    client: httpx.Client,
    *,
    base_url: str,
    model: str,
    message: str,
    session_id: Optional[str],
    session_key: str,
    enable_tools: bool,
) -> Optional[str]:
    payload: Dict[str, Any] = {
        "model": model,
        "message": message,
        "sessionKey": session_key,
        "enableTools": enable_tools,
    }
    if session_id:
        payload["sessionId"] = session_id

    next_session_id = session_id
    print("assistant> ", end="", flush=True)

    with client.stream("POST", f"{base_url.rstrip('/')}/api/chat", json=payload) as response:
        if response.status_code >= 400:
            print()
            print(f"HTTP {response.status_code}: {response.text}", file=sys.stderr)
            return next_session_id

        for line in response.iter_lines():
            data = _read_sse_payload(line)
            if not data:
                continue

            event_type = data.get("type")
            if event_type == "response_token":
                print(data.get("content", ""), end="", flush=True)
            elif event_type == "tool_use":
                print(f"\n[tool] {data.get('name')}({json.dumps(data.get('arguments', {}), ensure_ascii=False)})")
                print("assistant> ", end="", flush=True)
            elif event_type == "tool_result":
                print(f"\n[tool_result] {data.get('name')}: {data.get('result')}")
                print("assistant> ", end="", flush=True)
            elif event_type == "error":
                print()
                print(f"[error] {data.get('message')}", file=sys.stderr)
            elif event_type == "session_id":
                next_session_id = data.get("session_id") or next_session_id

    print()
    return next_session_id


def main() -> None:
    args = _parse_args()
    base_url = args.base_url.rstrip("/")
    model = args.model
    enable_tools = not args.no_tools
    session_id: Optional[str] = None

    print("Memoflow CLI chat")
    print(f"Backend: {base_url}")
    print(f"Model: {model}")
    print("Commands: /exit, /quit, /model <name>, /tools on, /tools off")

    with httpx.Client(timeout=None) as client:
        while True:
            try:
                message = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not message:
                continue
            if message in {"/exit", "/quit"}:
                break
            if message.startswith("/model "):
                model = message.split(" ", 1)[1].strip()
                session_id = None
                print(f"Switched model to {model}. Started a fresh backend session for this CLI.")
                continue
            if message == "/tools on":
                enable_tools = True
                print("Tools enabled.")
                continue
            if message == "/tools off":
                enable_tools = False
                print("Tools disabled.")
                continue

            session_id = _stream_turn(
                client,
                base_url=base_url,
                model=model,
                message=message,
                session_id=session_id,
                session_key=args.session_key,
                enable_tools=enable_tools,
            )


if __name__ == "__main__":
    main()
