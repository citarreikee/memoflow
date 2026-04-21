from fastapi import FastAPI


def register_system_routes(
    app: FastAPI,
    *,
    root,
    get_models,
    health_check,
    get_tools,
    api_info,
) -> None:
    app.add_api_route("/", root, methods=["GET"])
    app.add_api_route("/api/models", get_models, methods=["GET"])
    app.add_api_route("/api/health", health_check, methods=["GET"])
    app.add_api_route("/api/tools", get_tools, methods=["GET"])
    app.add_api_route("/api/info", api_info, methods=["GET"])
