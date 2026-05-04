import inspect
from typing import Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from api.chat_routes import register_chat_routes
from api.schemas import ChatRequest, SessionCreateRequest
from api.system_routes import register_system_routes
from chat_history import session_manager
from config import settings
from providers import react_orchestrator
from services.chat_service import (
    create_session_payload,
    delete_session_payload,
    get_session_payload,
    list_sessions_payload,
    prepare_chat_turn,
    stream_chat_with_session,
)
from services.model_catalog import ModelCatalogState, get_models_response
from services.system_service import (
    get_api_info_payload,
    get_health_payload,
    get_memory_checkpoint_payload,
    get_tools_payload,
)


app = FastAPI(title=settings.API_TITLE, version=settings.API_VERSION, description=settings.API_DESCRIPTION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS if settings.CORS_ORIGINS else ["*"],
    allow_origin_regex=settings.CORS_ALLOW_ORIGIN_REGEX if settings.CORS_ALLOW_ORIGIN_REGEX else None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

model_catalog_state = ModelCatalogState()


@app.on_event("startup")
async def load_persisted_sessions() -> None:
    session_manager._sessions.clear()
    session_manager._session_key_map.clear()
    from services.memory.session_store import session_store

    session_store.load_sessions_into(session_manager)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _run_with_500(action, fail_prefix: str):
    try:
        return await _maybe_await(action())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{fail_prefix}: {str(exc)}")


async def _run_with_404(action, not_found_detail: str = "Session not found"):
    try:
        return await _maybe_await(action())
    except KeyError:
        raise HTTPException(status_code=404, detail=not_found_detail)


async def root() -> Dict[str, str]:
    return {
        "message": "Memoflow Conversation API",
        "status": "running",
        "version": settings.API_VERSION,
        "docs": "/docs",
    }


async def get_models():
    try:
        return await get_models_response(model_catalog_state)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch models: {str(exc)}")


async def create_session(request: SessionCreateRequest):
    return await _run_with_500(
        lambda: create_session_payload(session_manager, request.model, request.metadata),
        "Failed to create session",
    )


async def list_sessions():
    return list_sessions_payload(session_manager)


async def get_session(session_id: str):
    return await _run_with_404(lambda: get_session_payload(session_manager, session_id))


async def delete_session(session_id: str):
    return await _run_with_404(lambda: delete_session_payload(session_manager, session_id))


async def chat_stream(request: ChatRequest):
    try:
        context = await prepare_chat_turn(
            session_manager,
            model=request.model,
            user_message=request.message,
            request_session_id=request.sessionId,
            request_session_key=request.sessionKey,
            input_source=request.inputSource,
        )
        return StreamingResponse(
            stream_chat_with_session(
                session_manager,
                react_orchestrator.stream_with_react,
                model=request.model,
                context=context,
                force_tool_use=request.forceToolUse,
                enable_tools=request.enableTools,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Streaming error: {str(exc)}")


async def health_check():
    return await get_health_payload()


async def get_tools():
    return await get_tools_payload()


async def api_info():
    return await _run_with_500(get_api_info_payload, "Failed to get info")


async def get_memory_checkpoint(session_id: str, checkpoint_id: str = ""):
    return await _run_with_404(
        lambda: get_memory_checkpoint_payload(session_id, checkpoint_id),
        "Session not found",
    )


register_system_routes(
    app,
    root=root,
    get_models=get_models,
    health_check=health_check,
    get_tools=get_tools,
    api_info=api_info,
    get_memory_checkpoint=get_memory_checkpoint,
)
register_chat_routes(
    app,
    create_session=create_session,
    list_sessions=list_sessions,
    get_session=get_session,
    delete_session=delete_session,
    chat_stream=chat_stream,
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.HOST, port=settings.PORT, log_level="info", timeout_graceful_shutdown=1)
