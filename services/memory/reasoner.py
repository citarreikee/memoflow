from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Dict, List

import httpx

from config import settings
from services.memory.harness import MemoryHarness
from services.memory.schemas import Episode


class MemoryReasonerError(Exception):
    """Raised when the memory sidecar model fails."""


@dataclass
class MemoryReasonerResult:
    memory_atoms: List[Dict[str, Any]]
    conversation_summary: str
    task_state_summary: str
    open_issues_summary: str


class MemoryReasoner:
    def __init__(self) -> None:
        self.provider = settings.MEMORY_MODEL_PROVIDER
        self.model = settings.MEMORY_MODEL_NAME
        self._warmed_up = False

    def reason(
        self,
        *,
        episode: Episode,
        harness: MemoryHarness,
        existing_conversation_summary: str,
        existing_task_state_summary: str,
        existing_open_issues_summary: str,
    ) -> MemoryReasonerResult:
        if self.provider != "ollama":
            raise MemoryReasonerError(f"Unsupported provider: {self.provider}")

        # This is the model-driven core of v0.1 memory processing. The sidecar
        # model is asked to do three things in one pass:
        # 1. extract durable memory atoms,
        # 2. update conversation summary,
        # 3. update task/open-issue summaries.
        prompt = self._build_prompt(
            episode=episode,
            harness=harness,
            existing_conversation_summary=existing_conversation_summary,
            existing_task_state_summary=existing_task_state_summary,
            existing_open_issues_summary=existing_open_issues_summary,
        )
        payload = self._call_ollama(prompt)
        return MemoryReasonerResult(
            memory_atoms=payload.get("memory_atoms") or [],
            conversation_summary=str(payload.get("conversation_summary") or "").strip(),
            task_state_summary=str(payload.get("task_state_summary") or "").strip(),
            open_issues_summary=str(payload.get("open_issues_summary") or "").strip(),
        )

    def _call_ollama(self, prompt: str) -> Dict[str, Any]:
        request_body = {
            "model": self.model,
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are MemoryReasoner for Memoflow. "
                        "Return only valid JSON. "
                        "Be conservative. "
                        "Do not invent facts. "
                        "Only produce durable, evidence-backed memory."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }
        last_error: Exception | None = None

        try:
            self._warmup()
        except Exception:
            pass

        # HTTP chat is the preferred fast path now that format=json has been
        # removed. We ask the model to emit ordinary text but end in one JSON
        # object, then parse that object ourselves.
        for attempt in range(3):
            try:
                with httpx.Client(timeout=180.0) as client:
                    response = client.post(f"{settings.OLLAMA_API_BASE}/api/chat", json=request_body)
                    response.raise_for_status()
                    payload = response.json()
                content = ((payload.get("message") or {}).get("content") or "").strip()
                if not content:
                    raise MemoryReasonerError("Memory model returned empty content")
                return self._parse_text_response(content)
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))

        try:
            return self._call_ollama_generate(prompt)
        except Exception as exc:
            last_error = exc

        # CLI is the last fallback when Ollama's HTTP path is unstable on this
        # machine. That keeps the memory pipeline usable even when the local
        # server intermittently returns 502 for this model.
        try:
            return self._call_ollama_cli(prompt)
        except Exception as exc:
            last_error = exc

        raise MemoryReasonerError(f"Ollama call failed for memory model {self.model}: {last_error}") from last_error

    def _warmup(self) -> None:
        if self._warmed_up:
            return
        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                f"{settings.OLLAMA_API_BASE}/api/generate",
                json={"model": self.model, "prompt": "ping", "stream": False},
            )
            response.raise_for_status()
        self._warmed_up = True

    def _call_ollama_generate(self, prompt: str) -> Dict[str, Any]:
        request_body = {
            "model": self.model,
            "prompt": (
                "You are MemoryReasoner for Memoflow.\n"
                "Return only valid JSON.\n"
                "Be conservative.\n"
                "Do not invent facts.\n"
                "Only produce durable, evidence-backed memory.\n\n"
                f"{prompt}"
            ),
            "stream": False,
        }
        with httpx.Client(timeout=180.0) as client:
            response = client.post(f"{settings.OLLAMA_API_BASE}/api/generate", json=request_body)
            response.raise_for_status()
            payload = response.json()
        content = str(payload.get("response") or "").strip()
        if not content:
            raise MemoryReasonerError("Memory model returned empty generate response")
        return self._parse_text_response(content)

    def _call_ollama_cli(self, prompt: str) -> Dict[str, Any]:
        cli_prompt = (
            "You are MemoryReasoner for Memoflow.\n"
            "Return only valid JSON.\n"
            "Be conservative.\n"
            "Do not invent facts.\n"
            "Only produce durable, evidence-backed memory.\n\n"
            f"{prompt}"
        )
        result = subprocess.run(
            [
                "ollama",
                "run",
                self.model,
                cli_prompt,
                "--nowordwrap",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=240,
            check=False,
        )
        combined = f"{result.stdout}\n{result.stderr}".strip()
        if result.returncode != 0 and not combined:
            raise MemoryReasonerError(f"Ollama CLI failed with exit code {result.returncode}")
        return self._parse_text_response(combined)

    def _parse_text_response(self, content: str) -> Dict[str, Any]:
        json_object = self._extract_last_json_object(content)
        if not json_object:
            raise MemoryReasonerError("Memory model returned no parseable JSON object")
        return self._parse_json_content(json_object)

    @staticmethod
    def _parse_json_content(content: str) -> Dict[str, Any]:
        try:
            result = json.loads(content)
        except json.JSONDecodeError as exc:
            raise MemoryReasonerError("Memory model returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise MemoryReasonerError("Memory model returned non-object JSON")
        return result

    @staticmethod
    def _extract_last_json_object(content: str) -> str:
        # Models may emit explanation or thinking-like text before the final JSON.
        # We therefore scan the whole response and keep the best dict candidate,
        # preferring the outer object that contains memory_atoms.
        cleaned = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", content or "")
        cleaned = re.sub(r"[^\S\r\n]+", " ", cleaned)
        decoder = json.JSONDecoder()
        candidate = ""
        best_len = -1
        for index, char in enumerate(cleaned):
            if char != "{":
                continue
            try:
                parsed, end = decoder.raw_decode(cleaned[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                snippet = cleaned[index : index + end]
                score = len(snippet)
                if "memory_atoms" in parsed:
                    score += 100000
                if score > best_len:
                    best_len = score
                    candidate = snippet
        return candidate.strip()

    @staticmethod
    def _build_prompt(
        *,
        episode: Episode,
        harness: MemoryHarness,
        existing_conversation_summary: str,
        existing_task_state_summary: str,
        existing_open_issues_summary: str,
    ) -> str:
        schema = {
            "memory_atoms": [
                {
                    "type": "semantic|project_state|preference|warning|procedural",
                    "content": "durable short memory",
                    "entities": ["entity"],
                    "keywords": ["keyword"],
                    "confidence": 0.0,
                    "importance": 0.0,
                }
            ],
            "conversation_summary": "updated summary",
            "task_state_summary": "updated task state",
            "open_issues_summary": "updated unresolved issues",
        }
        return (
            f"Harness: {harness.name}\n"
            f"Allowed memory types: {', '.join(harness.memory_types)}\n\n"
            "Existing short-term state:\n"
            f"- conversation_summary: {existing_conversation_summary or '<empty>'}\n"
            f"- task_state_summary: {existing_task_state_summary or '<empty>'}\n"
            f"- open_issues_summary: {existing_open_issues_summary or '<empty>'}\n\n"
            "Episode:\n"
            f"- source: {episode.source}\n"
            f"- user_message: {episode.user_message}\n"
            f"- assistant_answer: {episode.assistant_answer}\n"
            f"- tool_trace: {json.dumps(episode.tool_trace, ensure_ascii=False)}\n\n"
            "Instructions:\n"
            "1. Update summaries conservatively.\n"
            "2. Extract only durable memory worth retrieving later.\n"
            "3. Prefer project_state, warning, and procedural atoms for coding harness.\n"
            "4. If nothing is worth storing, return an empty memory_atoms list.\n"
            "5. Return JSON only using this shape:\n"
            f"{json.dumps(schema, ensure_ascii=False)}"
        )
