from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from .base import Tool


def _current_time(_: Dict[str, Any]) -> Dict[str, str]:
    now = datetime.now().astimezone()
    return {"iso": now.isoformat(), "timezone": str(now.tzinfo)}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get_openai_format(self) -> List[Dict[str, Any]]:
        return [tool.to_openai_format() for tool in self._tools.values()]

    def execute(self, name: str, arguments: Dict[str, Any]) -> Any:
        tool = self._tools.get(name)
        if not tool:
            raise KeyError(f"Unknown tool: {name}")
        return tool.handler(arguments)


registry = ToolRegistry()
registry.register(
    Tool(
        name="current_time",
        description="Get the server's current local time.",
        parameters={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        handler=_current_time,
    )
)
