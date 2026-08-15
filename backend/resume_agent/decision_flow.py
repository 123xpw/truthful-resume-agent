from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re

from .fragments import ResumeFragment, load_fragments
from .review_parser import INTERACTIVE_CONFIRMATION


CHOICE_LABELS = {
    "A": "A 本轮采用核心版",
    "B": "B 本轮采用保守版",
    "C": "C 本轮不使用这段经历",
    "D": "D 事实记录有误，需要回查事实库",
}


FACT_ID_RE = re.compile(r"^- fact_id:\s*`([^`]+)`", re.MULTILINE)
EVIDENCE_RE = re.compile(r"^- evidence:\s*(.*)$", re.MULTILINE)
BOUNDARY_RE = re.compile(r"^- boundaries:\s*(.*)$", re.MULTILINE)
MASTERY_LINE_RE = re.compile(r"^- mastery_check:\s*`.*?`", re.MULTILINE)
PENDING_MASTERY_RE = re.compile(r"^- mastery_check:\s*`(?:待确认|降权)`", re.MULTILINE)
UNSAFE_TERMINAL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x1b]")


def _extract_title(section: str) -> str:
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("###"):
            return stripped.lstrip("#").strip()
    return "Untitled fact"


def _replace_field(section: str, field: str, value: str) -> str:
    pattern = re.compile(rf"^- {re.escape(field)}:\s*.*$", re.MULTILINE)
    replacement = f"- {field}: {value}"
    if pattern.search(section):
        return pattern.sub(replacement, section, count=1)
    anchor = re.search(r"^- allowed_options:.*$", section, re.MULTILINE)
    if anchor:
        insert_at = anchor.end()
        return section[:insert_at] + "\n" + replacement + section[insert_at:]
    return section.rstrip() + "\n" + replacement + "\n"


def _terminal_safe(value: str) -> str:
    return UNSAFE_TERMINAL_CHARS_RE.sub("?", value)


def _terminal_bullet(value: str) -> str:
    value = re.sub(r"\\textbf\{([^{}]*)\}", r"\1", value)
    return _terminal_safe(value.replace(r"\#", "#").replace(r"\&", "&"))


def _find_fragment(project_root: Path | None, fact_ids: list[str]) -> ResumeFragment | None:
    if project_root is None:
        return None
    path = project_root / "data" / "resume_fragments" / "fragments.json"
    source_ids = set(fact_ids)
    return next(
        (
            fragment
            for fragment in load_fragments(path).values()
            if set(fragment.source_fact_ids) == source_ids
        ),
        None,
    )


def _print_wording_options(fragment: ResumeFragment | None) -> None:
    if fragment is None:
        print("\n当前事实没有可直接生成的简历片段；A/B 只记录本轮使用强度。")
        return
    print("\nA 核心版将写成：")
    for bullet in fragment.bullets.get("A", []):
        print(f"  - {_terminal_bullet(bullet)}")
    print("\nB 保守版将写成：")
    for bullet in fragment.bullets.get("B", []):
        print(f"  - {_terminal_bullet(bullet)}")


def _read_choice() -> str | None:
    while True:
        try:
            choice = input("请选择 A/B/C/D，直接回车跳过：").strip().upper()
        except EOFError:
            print("\n输入已结束，停止确认流程。")
            return None
        if choice == "":
            return ""
        if choice in CHOICE_LABELS:
            return choice
        print("输入无效，只能输入 A、B、C、D，或直接回车跳过。")


def _read_optional_text(prompt: str) -> tuple[str, bool]:
    try:
        return input(prompt).strip(), False
    except EOFError:
        print("\n输入已结束，保存已完成的确认并停止。")
        return "", True


def run_interactive_decision(
    review_path: Path,
    collect_notes: bool = False,
    project_root: Path | None = None,
) -> int:
    text = review_path.read_text(encoding="utf-8")
    starts = [match.start() for match in re.finditer(r"^(?:## |### )", text, re.MULTILINE)]
    starts.append(len(text))
    rebuilt: list[str] = [text[: starts[0]] if starts and starts[0] > 0 else ""]
    updated = 0

    for index in range(len(starts) - 1):
        section = text[starts[index] : starts[index + 1]]
        fact_ids = FACT_ID_RE.findall(section)
        if not fact_ids:
            rebuilt.append(section)
            continue
        if not PENDING_MASTERY_RE.search(section):
            rebuilt.append(section)
            continue

        title = _extract_title(section)
        evidence = EVIDENCE_RE.search(section)
        boundary = BOUNDARY_RE.search(section)

        print("\n" + "=" * 72)
        print(_terminal_safe(title))
        print(f"fact_id: {_terminal_safe(', '.join(fact_ids))}")
        if evidence:
            print(f"真实依据：{_terminal_safe(evidence.group(1))}")
        if boundary:
            print(f"边界风险：{_terminal_safe(boundary.group(1))}")
        _print_wording_options(_find_fragment(project_root, fact_ids))
        print("\n判断标准：")
        print("- facts.json 记录用户确认过的经历；这里决定本次投递采用哪套具体文案。")
        for label in CHOICE_LABELS.values():
            print(f"- {label}")

        choice = _read_choice()
        if choice is None:
            rebuilt.append(section)
            rebuilt.append(text[starts[index + 1] :])
            break
        if not choice:
            rebuilt.append(section)
            continue

        section = MASTERY_LINE_RE.sub(f"- mastery_check: `{CHOICE_LABELS[choice]}`", section, count=1)
        stop_after_current = False
        if collect_notes:
            can_explain, stop_after_current = _read_optional_text("你现在能讲清楚什么？直接回车可跳过：")
            if stop_after_current:
                cannot_explain = ""
            else:
                cannot_explain, stop_after_current = _read_optional_text("还有什么讲不清楚/需要复习？直接回车可跳过：")
        else:
            can_explain = ""
            cannot_explain = ""
        if can_explain:
            section = _replace_field(section, "what_i_can_explain", can_explain)
        if cannot_explain:
            section = _replace_field(section, "what_i_cannot_explain_yet", cannot_explain)
        intensity = "strong" if choice == "A" else "conservative" if choice == "B" else "blocked"
        section = _replace_field(section, "allowed_resume_intensity", intensity)
        section = _replace_field(section, "confirmed_via", f"`{INTERACTIVE_CONFIRMATION}`")
        section = _replace_field(section, "confirmed_at", f"`{datetime.now(timezone.utc).isoformat(timespec='seconds')}`")
        section = section.rstrip() + "\n\n"
        rebuilt.append(section)
        updated += 1
        if stop_after_current:
            rebuilt.append(text[starts[index + 1] :])
            break

    review_path.write_text("".join(rebuilt).lstrip("\n"), encoding="utf-8")
    print(f"\n已更新 {updated} 条确认结果：{review_path}")
    return 0
