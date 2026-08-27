"""Deterministic tests for the private context-strategy experiment."""

from __future__ import annotations

from .context_strategy_eval import build_messages, evaluate_run, evidence_for_mode
from .rules import Fact


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


FACTS = (
    Fact("fact_a", "Agent API", ("FastAPI", "Agent"), "Built a bounded Agent API.", ("single-user only",), "medium"),
    Fact("fact_b", "Spreadsheet", ("Excel",), "Automated spreadsheet reports.", (), "low"),
)
LABELS = {
    "fact_a": {"label": "useful", "rationale": "direct"},
    "fact_b": {"label": "irrelevant", "rationale": "unrelated"},
}


def test_full_context_contains_every_fact_without_keywords() -> None:
    evidence = evidence_for_mode("full_context", "FastAPI Agent", FACTS)
    _assert([item["fact_id"] for item in evidence] == ["fact_a", "fact_b"], "full context dropped a fact")
    _assert("keywords" not in evidence[0], "full context exposed retrieval-only metadata")


def test_keyword_context_reuses_production_top_k_logic() -> None:
    evidence = evidence_for_mode("keyword_top5", "FastAPI Agent", FACTS)
    _assert([item["fact_id"] for item in evidence] == ["fact_a"], "keyword context did not filter facts")


def test_scoring_rejects_unknown_ids_and_counts_relevance() -> None:
    response = (
        '{"selected":['
        '{"fact_id":"fact_a","relevance":"direct","reason":"API",'
        '"safe_claim":"Built an Agent API."},'
        '{"fact_id":"unknown","relevance":"partial","reason":"x",'
        '"safe_claim":"Unknown."}],"unsupported_requirements":[]}'
    )
    result = evaluate_run("full_context", "FastAPI", FACTS, LABELS, llm_call=lambda _messages: response)
    _assert(result.status == "completed", "valid response failed")
    _assert(result.useful_recall == 1.0 and result.useful_precision == 0.5, "invalid IDs did not penalize precision")
    _assert(result.unknown_fact_ids == ("unknown",), "unknown fact ID was not exposed")


def test_keyword_mode_rejects_known_but_withheld_fact_id() -> None:
    response = (
        '{"selected":['
        '{"fact_id":"fact_b","relevance":"partial","reason":"not supplied",'
        '"safe_claim":"Automated reports."}],"unsupported_requirements":[]}'
    )
    result = evaluate_run("keyword_top5", "FastAPI", FACTS, LABELS, llm_call=lambda _messages: response)
    _assert(result.out_of_context_fact_ids == ("fact_b",), "withheld fact was accepted as evidence")
    _assert(result.irrelevant_selected == 0 and result.supported_precision == 0.0, "withheld fact affected labels")


def test_malformed_model_output_fails_without_crashing_experiment() -> None:
    result = evaluate_run("keyword_top5", "FastAPI", FACTS, LABELS, llm_call=lambda _messages: "not-json")
    _assert(result.status == "failed", "malformed output did not fail closed")
    _assert("JSONDecodeError" in result.error, "failure reason was not retained")


def test_prompt_states_evidence_and_boundary_contract() -> None:
    messages = build_messages("Need FastAPI", evidence_for_mode("full_context", "Need FastAPI", FACTS))
    joined = "\n".join(message["content"] for message in messages)
    _assert("Use only the supplied evidence" in joined, "evidence boundary is absent")
    _assert("not proof that every compound" in joined, "compound-requirement warning is absent")


ALL_TESTS = [
    test_full_context_contains_every_fact_without_keywords,
    test_keyword_context_reuses_production_top_k_logic,
    test_scoring_rejects_unknown_ids_and_counts_relevance,
    test_keyword_mode_rejects_known_but_withheld_fact_id,
    test_malformed_model_output_fails_without_crashing_experiment,
    test_prompt_states_evidence_and_boundary_contract,
]


def main() -> int:
    for test in ALL_TESTS:
        test()
        print(f"PASS  {test.__name__}")
    print(f"\nAll {len(ALL_TESTS)} context strategy evaluation tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
