"""Deterministic analysis for the read-only Feishu application ledger."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import re
from typing import Any


FIELD_ALIASES = {
    "sequence": {"序号", "编号"},
    "company": {"公司名称", "公司"},
    "industry": {"行业分类", "行业"},
    "role": {"岗位名称", "职位名称", "岗位", "职位"},
    "applied_at": {"投递时间", "申请时间"},
    "exam_at": {"笔试时间"},
    "status": {"当前状态", "状态"},
    "channel": {"投递方式/内推码", "投递方式", "内推码"},
    "priority": {"优先级"},
    "next_action": {"下一步动作", "后续动作"},
    "notes": {"备注", "说明"},
}

PLANNING_STATUSES = {"待投递", "无合适岗位"}
ACTIVE_STATUSES = {
    "已投递",
    "简历筛选中",
    "笔试阶段",
    "面试阶段",
    "一面",
    "二面",
    "三面",
    "HR面",
    "等待结果",
}
CLOSED_STATUSES = {"已挂", "已拒绝", "拒绝", "不合适", "已结束", "已录用", "已入职"}
EXAM_STATUSES = {"笔试阶段"}

DASHBOARD_STAGE_ORDER = ("planning", "screening", "assessment", "interview", "shelved", "closed", "other")
DASHBOARD_STAGE_LABELS = {
    "planning": "待投递",
    "screening": "简历筛选中",
    "assessment": "笔试阶段",
    "interview": "面试阶段",
    "shelved": "暂时搁置",
    "closed": "已结束",
    "other": "未映射",
}
_DASHBOARD_STAGE_BY_STATUS = {
    "待投递": "planning",
    "已投递": "screening",
    "简历筛选中": "screening",
    "笔试阶段": "assessment",
    "面试阶段": "interview",
    "一面": "interview",
    "二面": "interview",
    "三面": "interview",
    "HR面": "interview",
    "等待结果": "interview",
    "无合适岗位": "shelved",
    "已挂": "closed",
    "已拒绝": "closed",
    "拒绝": "closed",
    "不合适": "closed",
    "已结束": "closed",
    "已录用": "closed",
    "已入职": "closed",
}
_PRIORITY_WEIGHT = {"高": 3, "中": 2, "低": 1}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _date_style(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    if isinstance(value, (int, float)) or text.isdigit():
        return "numeric"
    if re.fullmatch(r"\d{1,2}\.\d{1,2}", text):
        return "month-dot-day"
    if re.fullmatch(r"\d{1,2}月\d{1,2}日?", text):
        return "chinese-month-day"
    if "/" in text or "、" in text:
        return "slash-or-multiple"
    return "other"


def _field_mapping(headers: list[Any]) -> tuple[dict[str, int], list[str]]:
    normalized = [_text(value) for value in headers]
    mapping: dict[str, int] = {}
    for field, aliases in FIELD_ALIASES.items():
        index = next((i for i, header in enumerate(normalized) if header in aliases), None)
        if index is not None:
            mapping[field] = index
    missing = [field for field in ("company", "role", "applied_at", "status", "priority", "next_action") if field not in mapping]
    return mapping, missing


def extract_feishu_records(
    values: list[list[Any]],
    *,
    include_missing_sequence: bool = False,
) -> list[dict[str, Any]]:
    """Return normalized ledger rows with a stable, content-bound local identity."""
    if not values:
        return []
    mapping, missing_headers = _field_mapping(values[0])
    if missing_headers or "sequence" not in mapping:
        return []
    width = len(values[0])
    records: list[dict[str, Any]] = []
    for source_row, raw_row in enumerate(values[1:], start=2):
        row = list(raw_row) + [None] * max(0, width - len(raw_row))
        if not any(_text(value) for value in row):
            continue

        def field(name: str) -> str:
            index = mapping.get(name)
            return _text(row[index]) if index is not None and index < len(row) else ""

        sequence = field("sequence")
        if not sequence and not include_missing_sequence:
            continue
        display_sequence = sequence or f"行{source_row}"
        identity_payload = {
            "sequence": display_sequence,
            "company": field("company"),
            "role": field("role"),
            "applied_at": field("applied_at"),
        }
        identity = hashlib.sha256(
            json.dumps(identity_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        records.append(
            {
                "sequence": display_sequence,
                "sequence_missing": not bool(sequence),
                "source_row": source_row,
                "company": field("company"),
                "role": field("role"),
                "status": field("status"),
                "priority": field("priority"),
                "applied_at": field("applied_at"),
                "exam_at": field("exam_at"),
                "next_action": field("next_action"),
                "row_identity_sha256": identity,
            }
        )
    return records


def _dashboard_stage(status: str) -> str:
    return _DASHBOARD_STAGE_BY_STATUS.get(status, "other")


def _sequence_sort_key(sequence: str) -> tuple[int, str]:
    return (int(sequence), sequence) if sequence.isdigit() else (10**9, sequence)


def _focus_reason_and_action(record: dict[str, Any]) -> tuple[int, str, str] | None:
    stage = _dashboard_stage(record["status"])
    priority = record["priority"]
    if stage == "assessment":
        action = record["next_action"] or (
            "确认笔试安排并完成准备" if record["exam_at"] else "确认笔试截止时间并安排准备"
        )
        return 0, "已进入笔试阶段", action
    if stage == "interview":
        return 1, "已进入面试阶段", record["next_action"] or "确认面试安排并准备岗位相关案例"
    if stage == "screening" and priority == "高":
        return 2, "高优先级，正在筛选", record["next_action"] or "继续等待，同时补投同类岗位"
    if stage == "planning" and priority == "高":
        return 3, "高优先级，尚未投递", record["next_action"] or "确认岗位要求并优先完成投递"
    if stage in {"screening", "assessment", "interview"} and not record["role"]:
        return 4, "已进入投递流程，但岗位名称为空", record["next_action"] or "补充岗位名称，便于后续复盘"
    return None


def build_application_dashboard(records: list[dict[str, Any]]) -> dict[str, Any]:
    stage_counts = Counter(_dashboard_stage(record["status"]) for record in records)
    priority_by_stage: dict[str, dict[str, int]] = {
        stage: {"高": 0, "中": 0, "低": 0, "未设置": 0} for stage in DASHBOARD_STAGE_ORDER
    }
    for record in records:
        stage = _dashboard_stage(record["status"])
        priority = record["priority"] if record["priority"] in _PRIORITY_WEIGHT else "未设置"
        priority_by_stage[stage][priority] += 1

    ranked: list[tuple[int, int, tuple[int, str], dict[str, Any], str, str]] = []
    for record in records:
        focus = _focus_reason_and_action(record)
        if focus is None:
            continue
        group, reason, action = focus
        ranked.append(
            (
                group,
                -_PRIORITY_WEIGHT.get(record["priority"], 0),
                _sequence_sort_key(record["sequence"]),
                record,
                reason,
                action,
            )
        )
    ranked.sort(key=lambda item: (item[0], item[1], item[2]))
    focus_items = [
        {
            "sequence": record["sequence"],
            "company": record["company"],
            "role": record["role"],
            "status": record["status"],
            "priority": record["priority"],
            "reason": reason,
            "action": action,
        }
        for _, _, _, record, reason, action in ranked[:5]
    ]
    active_stages = {"screening", "assessment", "interview"}
    return {
        "metrics": {
            "active": sum(stage_counts[stage] for stage in active_stages),
            "assessment": stage_counts["assessment"],
            "active_high_priority": sum(
                record["priority"] == "高" and _dashboard_stage(record["status"]) in active_stages
                for record in records
            ),
            "planning": stage_counts["planning"],
        },
        "stage_counts": [
            {"key": stage, "label": DASHBOARD_STAGE_LABELS[stage], "count": stage_counts[stage]}
            for stage in DASHBOARD_STAGE_ORDER
            if stage_counts[stage]
        ],
        "priority_by_stage": [
            {
                "key": stage,
                "label": DASHBOARD_STAGE_LABELS[stage],
                "counts": priority_by_stage[stage],
            }
            for stage in DASHBOARD_STAGE_ORDER
            if stage_counts[stage]
        ],
        "focus_items": focus_items,
        "focus_policy": "assessment_then_interview_then_high_active_then_high_planning_then_missing_role",
    }


def analyze_feishu_values(values: list[list[Any]]) -> dict[str, Any]:
    if not values:
        return {
            "ready": False,
            "reason": "empty_snapshot",
            "field_mapping": {},
            "missing_required_headers": [],
            "total_records": 0,
            "status_counts": {},
            "warnings": [],
        }

    mapping, missing_headers = _field_mapping(values[0])
    if missing_headers:
        return {
            "ready": False,
            "reason": "missing_headers",
            "field_mapping": mapping,
            "missing_required_headers": missing_headers,
            "total_records": 0,
            "status_counts": {},
            "warnings": [f"缺少必要表头：{', '.join(missing_headers)}"],
        }

    width = len(values[0])
    rows = [list(row) + [None] * max(0, width - len(row)) for row in values[1:]]
    rows = [row for row in rows if any(_text(value) for value in row)]

    def value(row: list[Any], field: str) -> Any:
        index = mapping.get(field)
        return row[index] if index is not None and index < len(row) else None

    statuses = [_text(value(row, "status")) or "(空)" for row in rows]
    status_counts = Counter(statuses)
    planning = [row for row in rows if _text(value(row, "status")) in PLANNING_STATUSES]
    active = [row for row in rows if _text(value(row, "status")) in ACTIVE_STATUSES]
    closed = [row for row in rows if _text(value(row, "status")) in CLOSED_STATUSES]
    known = PLANNING_STATUSES | ACTIVE_STATUSES | CLOSED_STATUSES
    unknown_statuses = sorted({status for status in statuses if status not in known})
    applied = [
        row
        for row in rows
        if _text(value(row, "applied_at"))
        or _text(value(row, "status")) in (ACTIVE_STATUSES | CLOSED_STATUSES)
    ]

    missing_role_after_application = sum(not _text(value(row, "role")) for row in applied)
    missing_application_date = sum(not _text(value(row, "applied_at")) for row in applied)
    active_without_next_action = sum(not _text(value(row, "next_action")) for row in active)
    exam_without_date = sum(
        _text(value(row, "status")) in EXAM_STATUSES and not _text(value(row, "exam_at")) for row in rows
    )

    duplicate_keys = Counter(
        (_text(value(row, "company")), _text(value(row, "role")))
        for row in rows
        if _text(value(row, "company")) and _text(value(row, "role"))
    )
    duplicate_groups = sum(1 for count in duplicate_keys.values() if count > 1)
    date_styles = sorted(
        {
            style
            for row in rows
            if (style := _date_style(value(row, "applied_at"))) is not None
        }
    )
    dashboard = build_application_dashboard(extract_feishu_records(values, include_missing_sequence=True))

    warnings: list[str] = []
    if missing_role_after_application:
        warnings.append(f"{missing_role_after_application} 条已进入投递流程的记录缺少岗位名称。")
    if missing_application_date:
        warnings.append(f"{missing_application_date} 条已进入投递流程的记录缺少投递时间。")
    if active_without_next_action:
        warnings.append(f"{active_without_next_action} 条进行中记录尚未填写下一步动作。")
    if exam_without_date:
        warnings.append(f"{exam_without_date} 条笔试阶段记录缺少笔试时间。")
    if duplicate_groups:
        warnings.append(f"发现 {duplicate_groups} 组公司与岗位完全相同的重复记录。")
    if len(date_styles) > 1:
        warnings.append("投递时间存在多种格式，暂不据此计算超期提醒。")
    if unknown_statuses:
        warnings.append(f"发现未映射状态：{', '.join(unknown_statuses)}。")

    return {
        "ready": True,
        "reason": None,
        "field_mapping": mapping,
        "missing_required_headers": [],
        "total_records": len(rows),
        "planning_count": len(planning),
        "active_count": len(active),
        "closed_count": len(closed),
        "applied_count": len(applied),
        "status_counts": dict(status_counts),
        "priority_counts": dict(Counter(_text(value(row, "priority")) or "(空)" for row in rows)),
        "missing_role_after_application": missing_role_after_application,
        "missing_application_date": missing_application_date,
        "active_without_next_action": active_without_next_action,
        "exam_without_date": exam_without_date,
        "duplicate_groups": duplicate_groups,
        "application_date_styles": date_styles,
        "dashboard": dashboard,
        "warnings": warnings,
    }
