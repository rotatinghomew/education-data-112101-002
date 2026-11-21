"""汇总报告、可下钻明细与待复核名单的构建。"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from .evaluate import (
    GROUP_LABELS,
    NEGATIVE_STATUSES,
    STATUS_GROUPS,
    STATUS_LABELS,
    STATUS_ORDER,
    UNKNOWN_STATUSES,
    VERIFIABLE_STATUSES,
    ClaimAssessment,
    assess_scope,
)
from .loader import load_bundle
from .model import DataBundle

# 明细 CSV 的列顺序，保证从汇总可追到账号、人员、证据与当日状态。
DETAIL_FIELDS = [
    "claim_id",
    "status",
    "status_group",
    "status_label",
    "account_ref",
    "merchant_ref",
    "person_ref",
    "service_scope",
    "title_text",
    "claimed_at",
    "credential_ref",
    "credential_name",
    "issuer_ref",
    "issued_on",
    "expires_on",
    "credential_expired_on_date",
    "check_id",
    "check_source",
    "check_result",
    "checked_at",
    "check_valid_from",
    "check_valid_to",
    "evidence_note",
]

REVIEW_REASONS = {
    "no_credential": "未提交任何证件材料，需补录",
    "pending": "证件已提交但截至基准日无生效核验记录，需跟进核验",
    "unmatched": "核验无法匹配，需确认证件持证人或重新提交",
    "not_authoritative": "材料仅为培训/非权威来源记录，不构成头衔依据，需补权威证据",
    "expired": "证件已过期，需更新有效证件",
    "revoked": "证件已被撤销，需处置并复核声明",
}


def _iso(value) -> str:
    return value.isoformat() if value is not None else ""


def _evidence_note(item: ClaimAssessment) -> str:
    chosen = item.chosen_assessment()
    if item.status == "no_credential":
        return "声明人名下无证件记录"
    check = chosen.effective_check if chosen else None
    credential = chosen.credential if chosen else None
    parts = [f"证件 {credential.credential_ref}（{credential.credential_name}）"]
    if chosen and chosen.is_expired:
        parts.append(f"已于 {credential.expires_on.isoformat()} 过期")
    if check is None:
        parts.append("基准日无生效核验记录")
    else:
        parts.append(
            f"核验 {check.check_id} 来源 {check.source_ref} 结果 {check.result}"
        )
    return "；".join(parts)


def detail_row(item: ClaimAssessment) -> dict[str, str]:
    claim = item.claim
    chosen = item.chosen_assessment()
    credential = chosen.credential if chosen else None
    check = chosen.effective_check if chosen else None
    return {
        "claim_id": claim.claim_id,
        "status": item.status,
        "status_group": STATUS_GROUPS[item.status],
        "status_label": STATUS_LABELS[item.status],
        "account_ref": claim.account_ref,
        "merchant_ref": claim.merchant_ref,
        "person_ref": claim.person_ref,
        "service_scope": claim.service_scope,
        "title_text": claim.title_text,
        "claimed_at": _iso(claim.claimed_at),
        "credential_ref": credential.credential_ref if credential else "",
        "credential_name": credential.credential_name if credential else "",
        "issuer_ref": credential.issuer_ref if credential else "",
        "issued_on": _iso(credential.issued_on) if credential else "",
        "expires_on": _iso(credential.expires_on) if credential else "",
        "credential_expired_on_date": str(chosen.is_expired) if chosen else "",
        "check_id": check.check_id if check else "",
        "check_source": check.source_ref if check else "",
        "check_result": check.result if check else "",
        "checked_at": _iso(check.checked_at) if check else "",
        "check_valid_from": _iso(check.valid_from) if check else "",
        "check_valid_to": _iso(check.valid_to) if check else "",
        "evidence_note": _evidence_note(item),
    }


def build_report(
    bundle: DataBundle,
    on_date: date,
    service_scope: str | None = None,
    generated_at: str | None = None,
) -> dict:
    assessments, warnings = assess_scope(bundle, on_date, service_scope)
    total = len(assessments)

    counts = {status: 0 for status in STATUS_ORDER}
    for item in assessments:
        counts[item.status] += 1

    verifiable = sum(counts[s] for s in VERIFIABLE_STATUSES)
    negative = sum(counts[s] for s in NEGATIVE_STATUSES)
    unknown = sum(counts[s] for s in UNKNOWN_STATUSES)

    breakdown = []
    for status in STATUS_ORDER:
        breakdown.append(
            {
                "status": status,
                "label": STATUS_LABELS[status],
                "group": STATUS_GROUPS[status],
                "group_label": GROUP_LABELS[STATUS_GROUPS[status]],
                "count": counts[status],
                "share_of_denominator": round(counts[status] / total, 6) if total else None,
            }
        )

    details = [detail_row(item) for item in assessments]
    review = [
        {
            "claim_id": row["claim_id"],
            "account_ref": row["account_ref"],
            "person_ref": row["person_ref"],
            "service_scope": row["service_scope"],
            "title_text": row["title_text"],
            "status": row["status"],
            "status_label": row["status_label"],
            "reason": REVIEW_REASONS[row["status"]],
            "evidence_note": row["evidence_note"],
        }
        for row in details
        if row["status"] != "confirmed"
    ]

    active_account_ids = sorted({row["account_ref"] for row in details})
    return {
        "parameters": {
            "baseline_date": on_date.isoformat(),
            "service_scope": service_scope,
            "time_basis": "as_of",
            "scope_rule": (
                "基准日当日活跃（active_from <= 基准日，active_to 为空或 >= 基准日）账号下，"
                "声明日期不晚于基准日且服务范围匹配的全部账号×服务声明；逐条计数，不按人合并"
            ),
        },
        "denominator": {
            "total_claims": total,
            "active_accounts": len(active_account_ids),
            "active_account_refs": active_account_ids,
            "definition": (
                "可核验比例的分母 = 基准日在指定服务范围内活跃账号的全部在范围内声明，"
                "含已确认、有证据否定、未提交证件、无法匹配、待核验等所有状态；"
                "未知状态保留在分母中，不剔除、不计为无效"
            ),
        },
        "verifiability": {
            "numerator_statuses": sorted(VERIFIABLE_STATUSES),
            "numerator_claims": verifiable,
            "verifiable_ratio": round(verifiable / total, 6) if total else None,
            "negative_claims": negative,
            "unknown_claims": unknown,
            "note": "未知（待核验/未提交证件）不等于无效，单独列示",
        },
        "status_breakdown": breakdown,
        "details": details,
        "review_queue": review,
        "warnings": warnings,
        "generated_at": generated_at,
    }


def build_report_from_files(
    data_dir: str | Path,
    on_date: date,
    service_scope: str | None = None,
    generated_at: str | None = None,
) -> dict:
    generated_at = generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return build_report(load_bundle(data_dir), on_date, service_scope, generated_at)
