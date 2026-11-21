"""人类可读的中文文本报告渲染。"""

from __future__ import annotations

import unicodedata

from .evaluate import GROUP_LABELS, STATUS_GROUPS, STATUS_LABELS
from .report import DETAIL_FIELDS, REVIEW_REASONS


def _display_width(text: str) -> int:
    """中日韩全角字符按两列计算，其余按一列。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


def _pct(value) -> str:
    return "—" if value is None else f"{value * 100:.2f}%"


def render_text(report: dict) -> str:
    params = report["parameters"]
    denominator = report["denominator"]
    ver = report["verifiability"]
    lines: list[str] = []

    lines.append("资质声明可核验性分析（基准日时点口径）")
    lines.append("=" * 52)
    lines.append(f"基准日：{params['baseline_date']}")
    lines.append(f"服务范围：{params['service_scope'] or '全部服务范围'}")
    lines.append(f"生成时间：{report.get('generated_at') or '未记录'}")
    lines.append("")
    lines.append("一、统计口径与分母构成")
    lines.append("-" * 52)
    lines.append(params["scope_rule"])
    lines.append(denominator["definition"])
    lines.append(
        f"分母（进入统计的账号×服务声明数）：{denominator['total_claims']} 条，"
        f"涉及活跃账号 {denominator['active_accounts']} 个"
    )
    lines.append("")
    lines.append("二、状态汇总（七类互斥，未知不计为无效）")
    lines.append("-" * 52)
    lines.append(f"{'状态':<20}{'分组':<16}{'数量':>6}{'占分母':>10}")
    for row in report["status_breakdown"]:
        lines.append(
            f"{_pad(row['label'], 20)}{_pad(row['group_label'], 16)}"
            f"{row['count']:>6}{_pct(row['share_of_denominator']):>10}"
        )
    lines.append("")
    lines.append("三、可核验比例")
    lines.append("-" * 52)
    lines.append(f"分子（状态：{', '.join(ver['numerator_statuses'])}）：{ver['numerator_claims']} 条")
    lines.append(f"分母：{denominator['total_claims']} 条（定义见上，含全部状态）")
    lines.append(f"可核验比例：{_pct(ver['verifiable_ratio'])}")
    lines.append(
        f"其中有证据否定 {ver['negative_claims']} 条；"
        f"未知 {ver['unknown_claims']} 条（{ver['note']}）"
    )
    lines.append("")
    lines.append("四、声明明细（可由汇总下钻至账号、人员、证据与当日状态）")
    lines.append("-" * 52)
    compact_cols = [
        "claim_id",
        "status_label",
        "account_ref",
        "person_ref",
        "service_scope",
        "title_text",
        "credential_ref",
        "check_id",
        "check_result",
        "expires_on",
    ]
    header = " | ".join(col for col in compact_cols)
    lines.append(header)
    lines.append("-" * _display_width(header))
    for row in report["details"]:
        lines.append(" | ".join((row.get(col) or "") for col in compact_cols))
    lines.append("")
    lines.append(f"五、待复核名单（{len(report['review_queue'])} 条，不含已确认）")
    lines.append("-" * 52)
    if not report["review_queue"]:
        lines.append("无")
    for row in report["review_queue"]:
        lines.append(
            f"{row['claim_id']}｜{row['account_ref']}｜{row['person_ref']}｜"
            f"{row['status_label']}｜{REVIEW_REASONS[row['status']]}"
        )
        lines.append(f"    证据说明：{row['evidence_note']}")
    if report["warnings"]:
        lines.append("")
        lines.append("六、数据质量告警")
        lines.append("-" * 52)
        for warning in report["warnings"]:
            lines.append(f"- {warning}")
    return "\n".join(lines)


def render_review_list(report: dict) -> str:
    lines = [f"待复核名单（{len(report['review_queue'])} 条，不含已确认声明）", "=" * 52]
    if not report["review_queue"]:
        lines.append("无")
        return "\n".join(lines)
    for row in report["review_queue"]:
        lines.append(
            f"{row['claim_id']}｜{row['account_ref']}｜{row['person_ref']}｜"
            f"{row['service_scope']}｜{row['status_label']}｜{REVIEW_REASONS[row['status']]}"
        )
        lines.append(f"    证据说明：{row['evidence_note']}")
    return "\n".join(lines)


def render_review_csv_rows(report: dict) -> list[dict[str, str]]:
    return report["review_queue"]


def detail_fieldnames() -> list[str]:
    return list(DETAIL_FIELDS)


__all__ = [
    "render_text",
    "render_review_list",
    "render_review_csv_rows",
    "detail_fieldnames",
    "STATUS_LABELS",
    "STATUS_GROUPS",
    "GROUP_LABELS",
]
