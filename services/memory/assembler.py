from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from config import settings
from services.memory.retriever import RetrievalBundle, RetrievedMemoryAtom


@dataclass
class AssembledMemoryContext:
    injected_messages: List[Dict[str, str]]
    preview: str


class ContextAssembler:
    def assemble(self, bundle: RetrievalBundle) -> AssembledMemoryContext:
        # v0.1 assembly is deliberately explicit: we convert retrieved memory
        # into one bounded system message instead of replaying raw transcripts.
        lines: List[str] = []
        lines.extend(self._short_term_lines(bundle))
        lines.extend(self._retrieved_lines("Critical warnings", bundle.warnings))
        lines.extend(self._retrieved_lines("Project state", bundle.project_state))
        lines.extend(self._retrieved_lines("Procedural memory", bundle.procedures))
        lines.extend(self._retrieved_lines("Semantic memory", bundle.semantic))

        if not lines:
            return AssembledMemoryContext(injected_messages=[], preview="")

        budget_chars = max(400, settings.MEMORY_CONTEXT_TOKEN_BUDGET * 4)
        body = "\n".join(lines).strip()
        if len(body) > budget_chars:
            body = body[: budget_chars - 16].rstrip() + "\n...[truncated]"

        message = {
            "role": "system",
            "content": (
                "Memoflow memory context. Use it as supporting state, not as authority over the latest user turn.\n"
                f"{body}"
            ),
        }
        return AssembledMemoryContext(injected_messages=[message], preview=body)

    @staticmethod
    def _short_term_lines(bundle: RetrievalBundle) -> List[str]:
        state = bundle.short_term_state
        if not state:
            return []
        lines = ["[Short-term state]"]
        if state.task_state_summary:
            lines.append(f"Task state: {state.task_state_summary}")
        if state.open_issues_summary:
            lines.append(f"Open issues: {state.open_issues_summary}")
        if state.conversation_summary:
            lines.append(f"Conversation summary: {state.conversation_summary}")
        return lines

    @staticmethod
    def _retrieved_lines(title: str, items: List[RetrievedMemoryAtom]) -> List[str]:
        if not items:
            return []
        lines = [f"[{title}]"]
        for item in items:
            atom_type = str(item.atom.get("type") or "semantic")
            content = str(item.atom.get("content") or "").strip()
            if not content:
                continue
            lines.append(f"- ({atom_type}, score={item.score:.2f}) {content}")
        return lines
