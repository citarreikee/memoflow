from __future__ import annotations

from typing import List

from services.memory.harness import MemoryHarness
from services.memory.reasoner import MemoryReasoner, MemoryReasonerError
from services.memory.schemas import Episode, ShortTermState, utc_now_iso
from services.memory.stores import SQLiteMemoryStore


class ShortTermContextManager:
    def __init__(self, store: SQLiteMemoryStore, reasoner: MemoryReasoner | None = None) -> None:
        self.store = store
        self.reasoner = reasoner

    def load_or_create(self, session_id: str, harness: MemoryHarness) -> ShortTermState:
        state = self.store.get_short_term_state(session_id)
        if state:
            return state
        return ShortTermState(session_id=session_id, harness=harness.name)

    def update_after_episode(self, episode: Episode, harness: MemoryHarness) -> ShortTermState:
        state = self.load_or_create(episode.session_id, harness)
        recent_turn_ids = [turn_id for turn_id in state.recent_turn_ids if turn_id != episode.id]
        recent_turn_ids.append(episode.id)
        compacted_turn_ids: List[str] = []
        if len(recent_turn_ids) > harness.recent_turn_limit:
            compacted_turn_ids = recent_turn_ids[: -harness.recent_turn_limit]
            recent_turn_ids = recent_turn_ids[-harness.recent_turn_limit :]

        reasoner_result = None
        if self.reasoner:
            try:
                # Short-term state is model-driven already: the sidecar reads the
                # finished episode plus existing summaries and returns updated
                # conversation/task/open-issue state in one pass.
                reasoner_result = self.reasoner.reason(
                    episode=episode,
                    harness=harness,
                    existing_conversation_summary=state.conversation_summary,
                    existing_task_state_summary=state.task_state_summary,
                    existing_open_issues_summary=state.open_issues_summary,
                )
            except MemoryReasonerError:
                reasoner_result = None

        if compacted_turn_ids:
            state.covered_message_ids = sorted(set(state.covered_message_ids + compacted_turn_ids))
            state.conversation_summary = (
                reasoner_result.conversation_summary
                if reasoner_result and reasoner_result.conversation_summary
                else self._append_compaction_note(state.conversation_summary, compacted_turn_ids)
            )
        elif reasoner_result and reasoner_result.conversation_summary:
            state.conversation_summary = reasoner_result.conversation_summary

        state.recent_turn_ids = recent_turn_ids
        state.harness = harness.name
        state.task_state_summary = (
            reasoner_result.task_state_summary
            if reasoner_result
            and reasoner_result.task_state_summary
            and reasoner_result.task_state_summary.strip().lower() not in {"empty", "none", "n/a"}
            else self._derive_task_state_hint(episode, state.task_state_summary)
        )
        state.open_issues_summary = (
            reasoner_result.open_issues_summary
            if reasoner_result
            and reasoner_result.open_issues_summary
            and reasoner_result.open_issues_summary.strip().lower() not in {"empty", "none", "n/a"}
            else self._derive_open_issue_hint(episode, state.open_issues_summary)
        )
        state.updated_at = utc_now_iso()
        self.store.save_short_term_state(state)
        return state

    @staticmethod
    def _append_compaction_note(existing: str, compacted_turn_ids: List[str]) -> str:
        note = f"Compacted {len(compacted_turn_ids)} older turn(s): {', '.join(compacted_turn_ids)}."
        if not existing:
            return note
        return f"{existing}\n{note}"

    @staticmethod
    def _derive_task_state_hint(episode: Episode, existing: str) -> str:
        if not episode.assistant_answer:
            return existing
        answer = episode.assistant_answer.strip().replace("\n", " ")
        if len(answer) > 300:
            answer = answer[:300] + "..."
        return f"Last completed turn: {answer}"

    @staticmethod
    def _derive_open_issue_hint(episode: Episode, existing: str) -> str:
        # Rule fallback still exists here, but only as degradation behavior when
        # the sidecar model does not return a useful open-issues summary.
        text = f"{episode.user_message}\n{episode.assistant_answer}".lower()
        markers = ("todo", "next", "fix", "error", "failed", "blocked", "issue", "未完成", "下一步", "错误")
        if any(marker in text for marker in markers):
            user = episode.user_message.strip().replace("\n", " ")
            if len(user) > 220:
                user = user[:220] + "..."
            return f"Potential open issue from latest turn: {user}"
        return existing
