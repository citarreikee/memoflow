from fastapi import FastAPI


def register_memory_routes(
    app: FastAPI,
    *,
    memory_status,
    list_harnesses,
    list_episodes,
    get_episode,
    get_short_term_state,
    list_short_term_states,
    list_memory_atoms,
    get_memory_atom,
    search_memory,
    list_retrieval_events,
) -> None:
    app.add_api_route("/api/memory/status", memory_status, methods=["GET"])
    app.add_api_route("/api/memory/harnesses", list_harnesses, methods=["GET"])
    app.add_api_route("/api/memory/episodes", list_episodes, methods=["GET"])
    app.add_api_route("/api/memory/episodes/{episode_id}", get_episode, methods=["GET"])
    app.add_api_route("/api/memory/short-term", list_short_term_states, methods=["GET"])
    app.add_api_route("/api/memory/short-term/{session_id}", get_short_term_state, methods=["GET"])
    app.add_api_route("/api/memory/atoms", list_memory_atoms, methods=["GET"])
    app.add_api_route("/api/memory/atoms/{atom_id}", get_memory_atom, methods=["GET"])
    app.add_api_route("/api/memory/search", search_memory, methods=["POST"])
    app.add_api_route("/api/memory/retrieval-events", list_retrieval_events, methods=["GET"])
