from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.memory.formation.extractor import (
    extract_candidates_rule_based,
    parse_candidate_json,
    parse_candidate_payload,
    should_trigger_extraction,
)
from services.memory.formation.quality import MAX_CANDIDATE_TEXT_CHARS, postprocess_candidates
from services.memory.formation.schemas import MemoryCandidateLite


def test_plain_text_json_fragment_parses() -> None:
    raw = "The useful payload is below:\n```json\n{\"candidates\":[{\"text\":\"Decision: use quality gates before lifecycle work.\",\"type\":\"decision\",\"scope\":\"project\",\"action\":\"ADD\",\"importance\":0.88,\"reason\":\"Roadmap safety.\",\"stability\":\"evolving\"}]}\n```"
    payload = parse_candidate_json(raw)
    candidates = parse_candidate_payload(payload, max_candidates=3)

    assert len(candidates) == 1
    assert candidates[0].type == "decision"
    assert candidates[0].scope == "project"


def test_parser_prefers_candidate_payload_over_note_json() -> None:
    raw = (
        "I found one useful memory. Meta: {\"note\": \"not the payload\"}.\n"
        "Payload follows:\n"
        "{\"candidates\":[{\"text\":\"Project rule: do not enable memory writes by default.\","
        "\"type\":\"project_rule\",\"scope\":\"project\",\"action\":\"ADD\","
        "\"importance\":0.91,\"reason\":\"Safety default.\",\"stability\":\"stable\"}]}"
    )
    payload = parse_candidate_json(raw)
    candidates = parse_candidate_payload(payload, max_candidates=3)

    assert len(candidates) == 1
    assert candidates[0].type == "project_rule"
    assert candidates[0].scope == "project"



def test_parser_accepts_json_array_payload() -> None:
    raw = (
        "Candidates:\n"
        "[{\"text\":\"Decision: retrieval planner should be optional.\","
        "\"type\":\"decision\",\"scope\":\"project\",\"action\":\"ADD\","
        "\"importance\":0.81,\"reason\":\"Keeps runtime deterministic.\",\"stability\":\"evolving\"}]"
    )
    payload = parse_candidate_json(raw)
    candidates = parse_candidate_payload(payload, max_candidates=3)

    assert len(candidates) == 1
    assert candidates[0].type == "decision"



def test_parser_handles_braces_inside_strings() -> None:
    raw = (
        "Natural note before JSON. "
        "{\"candidates\":[{\"text\":\"Decision: keep literal braces like {example} inside memory text.\","
        "\"type\":\"decision\",\"scope\":\"project\",\"action\":\"ADD\","
        "\"importance\":0.8,\"reason\":\"Parser robustness.\",\"stability\":\"evolving\"}]}"
    )
    payload = parse_candidate_json(raw)
    candidates = parse_candidate_payload(payload, max_candidates=3)

    assert len(candidates) == 1
    assert "{example}" in candidates[0].text


def test_candidate_quality_dedupes_and_keeps_stronger_item() -> None:
    weak = MemoryCandidateLite(
        text="Decision: keep memory retrieval default off.",
        type="decision",
        scope="project",
        action="ADD",
        importance=0.6,
        reason="",
        stability="unknown",
        candidate_id="weak",
    )
    strong = MemoryCandidateLite(
        text="Decision: keep memory retrieval default off.",
        type="decision",
        scope="project",
        action="ADD",
        importance=0.9,
        reason="Avoids prompt pollution before quality gates pass.",
        stability="stable",
        candidate_id="strong",
    )

    candidates, report = postprocess_candidates([weak, strong], max_candidates=3)

    assert len(candidates) == 1
    assert candidates[0].candidate_id == "strong"
    assert report.duplicate_count == 1
    assert report.input_count == 2
    assert report.output_count == 1


def test_candidate_quality_clips_and_drops_empty() -> None:
    long_text = "A" * (MAX_CANDIDATE_TEXT_CHARS + 50)
    candidates, report = postprocess_candidates(
        [
            MemoryCandidateLite(
                text="   ",
                type="decision",
                scope="project",
                action="ADD",
                importance=0.8,
                reason="empty text",
                stability="stable",
                candidate_id="empty",
            ),
            MemoryCandidateLite(
                text=long_text,
                type="decision",
                scope="project",
                action="ADD",
                importance=0.8,
                reason="  Useful reason.  ",
                stability="stable",
                candidate_id="long",
            ),
        ],
        max_candidates=3,
    )

    assert len(candidates) == 1
    assert len(candidates[0].text) == MAX_CANDIDATE_TEXT_CHARS
    assert candidates[0].text.endswith("...")
    assert candidates[0].reason == "Useful reason."
    assert report.dropped_empty_count == 1
    assert report.clipped_text_count == 1


def test_candidate_quality_applies_max_candidates_after_dedupe() -> None:
    raw_candidates = [
        MemoryCandidateLite(
            text=f"Decision: quality case {index}.",
            type="decision",
            scope="project",
            action="ADD",
            importance=0.8,
            reason="test",
            stability="stable",
            candidate_id=f"cand_{index}",
        )
        for index in range(5)
    ]

    candidates, report = postprocess_candidates(raw_candidates, max_candidates=2)

    assert len(candidates) == 2
    assert "max_candidates_applied" in report.notes


def test_rule_extractor_uses_readable_chinese_markers() -> None:
    episode = {
        "episode_id": "ep_cn",
        "turn_index": 1,
        "messages": [
            {"role": "user", "content": "请记住：用户偏好是先给结论，再给验证结果。"},
            {"role": "assistant", "content": "收到。"},
        ],
    }

    assert should_trigger_extraction(episode)
    candidates = extract_candidates_rule_based(episode, max_candidates=3)
    assert candidates
    assert any(candidate.type == "preference" for candidate in candidates)


def main() -> None:
    test_plain_text_json_fragment_parses()
    test_parser_prefers_candidate_payload_over_note_json()
    test_parser_accepts_json_array_payload()
    test_parser_handles_braces_inside_strings()
    test_candidate_quality_dedupes_and_keeps_stronger_item()
    test_candidate_quality_clips_and_drops_empty()
    test_candidate_quality_applies_max_candidates_after_dedupe()
    test_rule_extractor_uses_readable_chinese_markers()
    print("memory formation quality smoke ok")


if __name__ == "__main__":
    main()
