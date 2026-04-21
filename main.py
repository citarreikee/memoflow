import inspect
from typing import Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from api.chat_routes import register_chat_routes
from api.memory_routes import register_memory_routes
from api.schemas import ChatRequest, MemorySearchRequest, SessionCreateRequest
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
from services.memory.api_service import (
    get_episode_payload,
    get_memory_atom_payload,
    get_short_term_state_payload,
    list_episodes_payload,
    list_harnesses_payload,
    list_memory_atoms_payload,
    list_short_term_states_payload,
    memory_status_payload,
    search_memory_payload,
)
from services.system_service import get_api_info_payload, get_health_payload, get_tools_payload


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
        "memory_processing": "disabled",
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
        context = prepare_chat_turn(
            session_manager,
            model=request.model,
            user_message=request.message,
            request_session_id=request.sessionId,
            request_session_key=request.sessionKey,
            input_source=request.inputSource,
            request_harness=request.memoryHarness,
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


async def memory_status():
    return memory_status_payload()


async def list_memory_harnesses():
    return list_harnesses_payload()


async def list_memory_episodes(session_id: str | None = None, limit: int = 50):
    return await _run_with_500(lambda: list_episodes_payload(session_id=session_id, limit=limit), "Failed to list episodes")


async def get_memory_episode(episode_id: str):
    return await _run_with_404(lambda: get_episode_payload(episode_id), "Episode not found")


async def get_memory_short_term_state(session_id: str):
    return await _run_with_404(lambda: get_short_term_state_payload(session_id), "Short-term state not found")


async def list_memory_short_term_states(limit: int = 50):
    return await _run_with_500(lambda: list_short_term_states_payload(limit=limit), "Failed to list short-term states")


async def list_memory_atoms(scope_type: str | None = None, scope_id: str | None = None, limit: int = 50):
    return await _run_with_500(
        lambda: list_memory_atoms_payload(scope_type=scope_type, scope_id=scope_id, limit=limit),
        "Failed to list memory atoms",
    )


async def get_memory_atom(atom_id: str):
    return await _run_with_404(lambda: get_memory_atom_payload(atom_id), "Memory atom not found")


async def search_memory(request: MemorySearchRequest):
    return await _run_with_500(
        lambda: search_memory_payload(
            query=request.query,
            scope_type=request.scope_type,
            scope_id=request.scope_id,
            limit=request.limit,
        ),
        "Failed to search memory",
    )


register_system_routes(
    app,
    root=root,
    get_models=get_models,
    health_check=health_check,
    get_tools=get_tools,
    api_info=api_info,
)
register_chat_routes(
    app,
    create_session=create_session,
    list_sessions=list_sessions,
    get_session=get_session,
    delete_session=delete_session,
    chat_stream=chat_stream,
)
register_memory_routes(
    app,
    memory_status=memory_status,
    list_harnesses=list_memory_harnesses,
    list_episodes=list_memory_episodes,
    get_episode=get_memory_episode,
    get_short_term_state=get_memory_short_term_state,
    list_short_term_states=list_memory_short_term_states,
    list_memory_atoms=list_memory_atoms,
    get_memory_atom=get_memory_atom,
    search_memory=search_memory,
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.HOST, port=settings.PORT, log_level="info", timeout_graceful_shutdown=1)
