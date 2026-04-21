from fastapi import FastAPI


def register_chat_routes(
    app: FastAPI,
    *,
    create_session,
    list_sessions,
    get_session,
    delete_session,
    chat_stream,
) -> None:
    app.add_api_route("/api/sessions", create_session, methods=["POST"])
    app.add_api_route("/api/sessions", list_sessions, methods=["GET"])
    app.add_api_route("/api/sessions/{session_id}", get_session, methods=["GET"])
    app.add_api_route("/api/sessions/{session_id}", delete_session, methods=["DELETE"])
    app.add_api_route("/api/chat", chat_stream, methods=["POST"])
