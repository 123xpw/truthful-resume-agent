"""Deterministic tests for frozen matcher cohorts and compact audit labels."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from .eval_matchers import load_audit_labels, load_cohort_paths, validate_label_coverage


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_grouped_labels_expand_to_complete_legacy_matrix() -> None:
    with TemporaryDirectory(prefix="matcher-labels-") as temp_dir:
        path = Path(temp_dir) / "labels.json"
        path.write_text(
            json.dumps(
                {
                    "reviewer": "test-reviewer",
                    "cases": {
                        "a.md": {
                            "rationale": "case-level audit reason",
                            "useful": ["fact_a"],
                            "marginal": ["fact_b"],
                            "irrelevant": [],
                            "notes": {"fact_b": "specific boundary note"},
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        reviewer, cases = load_audit_labels(path)
    _assert(reviewer == "test-reviewer", "reviewer was not retained")
    _assert(cases["a.md"]["fact_a"] == {"label": "useful", "rationale": "case-level audit reason"}, "group label expansion failed")
    _assert(cases["a.md"]["fact_b"]["rationale"] == "specific boundary note", "per-fact note was ignored")


def test_grouped_labels_reject_duplicate_fact_assignment() -> None:
    with TemporaryDirectory(prefix="matcher-labels-duplicate-") as temp_dir:
        path = Path(temp_dir) / "labels.json"
        path.write_text(
            json.dumps(
                {
                    "cases": {
                        "a.md": {
                            "rationale": "reason",
                            "useful": ["fact_a"],
                            "marginal": ["fact_a"],
                            "irrelevant": [],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        try:
            load_audit_labels(path)
        except ValueError as exc:
            _assert("duplicate grouped label" in str(exc), "duplicate returned the wrong error")
        else:
            raise AssertionError("duplicate grouped label was accepted")


def test_cohort_manifest_is_ordered_and_path_bounded() -> None:
    with TemporaryDirectory(prefix="matcher-cohort-") as temp_dir:
        root = Path(temp_dir)
        jd_dir = root / "data" / "jd_library"
        jd_dir.mkdir(parents=True)
        (jd_dir / "b.md").write_text("# B", encoding="utf-8")
        (jd_dir / "a.md").write_text("# A", encoding="utf-8")
        cohort = root / "cohort.json"
        cohort.write_text(json.dumps({"cases": [{"file": "b.md"}, {"file": "a.md"}]}), encoding="utf-8")
        paths = load_cohort_paths(root, cohort)
        _assert([path.name for path in paths] == ["b.md", "a.md"], "frozen cohort order changed")

        cohort.write_text(json.dumps({"cases": [{"file": "../outside.md"}]}), encoding="utf-8")
        try:
            load_cohort_paths(root, cohort)
        except ValueError as exc:
            _assert("invalid cohort JD path" in str(exc), "unsafe path returned the wrong error")
        else:
            raise AssertionError("cohort path traversal was accepted")


def test_local_v2_labels_cover_every_frozen_pair_when_present() -> None:
    root = Path(__file__).resolve().parents[2]
    cohort = root / "data" / "evaluation" / "target_matcher_cohort.v2.json"
    labels = root / "data" / "evaluation" / "target_matcher_labels.v2.json"
    # The target cohort records private application history and is intentionally
    # absent in a clean public clone. Generic format/path tests above still run
    # in CI; this adds full local coverage whenever the private files exist.
    if not cohort.is_file() or not labels.is_file():
        return
    raw_cohort = json.loads(cohort.read_text(encoding="utf-8"))
    reviewer, cases = load_audit_labels(labels)
    fact_ids = set(next(iter(cases.values())))
    jd_names = {item["file"] for item in raw_cohort["cases"]}
    fake_paths = [Path(name) for name in sorted(jd_names)]
    errors = validate_label_coverage(fake_paths, fact_ids, cases)
    _assert(reviewer == "codex_audit_2026-08-27", "v2 reviewer changed unexpectedly")
    _assert(len(jd_names) == 22 and len(fact_ids) == 11, "frozen v2 matrix dimensions changed")
    _assert(not errors, f"v2 label coverage is incomplete: {errors}")


ALL_TESTS = [
    test_grouped_labels_expand_to_complete_legacy_matrix,
    test_grouped_labels_reject_duplicate_fact_assignment,
    test_cohort_manifest_is_ordered_and_path_bounded,
    test_local_v2_labels_cover_every_frozen_pair_when_present,
]


def main() -> int:
    for test in ALL_TESTS:
        test()
        print(f"PASS  {test.__name__}")
    print(f"\n全部 {len(ALL_TESTS)} 项 matcher evaluation 测试通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
