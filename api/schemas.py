from typing import Any, Dict, Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    model: str
    message: str
    sessionId: Optional[str] = None
    sessionKey: Optional[str] = None
    inputSource: Optional[str] = None
    forceToolUse: bool = False
    enableTools: bool = True


class SessionCreateRequest(BaseModel):
    model: str
    metadata: Optional[Dict[str, Any]] = None
