"""基准日时点的声明状态判定。

状态口径（七类互斥，未知不并入无效）：

1. confirmed      已确认：证件在基准日未过期，且当日有效的核验结果为 issuer_confirmed
2. revoked        已撤销：当日有效的核验结果为 revoked
3. unmatched      无法匹配：当日有效核验结果为 unmatched（如证件属于其他持证人）
4. not_authoritative 依据不具权威性：当日有效核验结果为 not_authoritative，
                   典型场景是只拿到培训记录/结业证明，不构成头衔成立依据
5. expired        已过期：证件 expires_on 早于基准日，且当日核验为确认或无否定性核验
6. pending        尚未完成核验：已提交证件，但没有任何在基准日生效的核验记录
7. no_credential  未提交证件：该声明人名下没有任何证件材料

优先级：否定性核验结论（revoked/unmatched/not_authoritative）优先于证件过期；
过期只压过 issuer_confirmed（确认的证件必须本身仍在有效期内）。
判定严格基于基准日当日：核验记录按 valid_from/valid_to 选当日有效版本，
supersedes 版本链只在同日存在多个有效版本时用于选择新记录；
证件有效期按 expires_on 当日仍有效、次日起过期处理。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .model import Check, Claim, Credential, DataBundle

# 汇总与展示顺序：先可核验，再否定性证据，最后未知类。
STATUS_ORDER = (
    "confirmed",
    "revoked",
    "unmatched",
    "not_authoritative",
    "expired",
    "pending",
    "no_credential",
)

STATUS_LABELS = {
    "confirmed": "已确认（可核验）",
    "revoked": "已撤销",
    "expired": "已过期",
    "unmatched": "无法匹配证件",
    "not_authoritative": "依据不具权威性",
    "pending": "尚未完成核验",
    "no_credential": "未提交证件",
}

# 分组：可核验 / 有证据否定 / 未知（不得计入无效）。
STATUS_GROUPS = {
    "confirmed": "verifiable",
    "revoked": "negative",
    "expired": "negative",
    "unmatched": "negative",
    "not_authoritative": "negative",
    "pending": "unknown",
    "no_credential": "unknown",
}

VERIFIABLE_STATUSES = frozenset(s for s, g in STATUS_GROUPS.items() if g == "verifiable")
NEGATIVE_STATUSES = frozenset(s for s, g in STATUS_GROUPS.items() if g == "negative")
UNKNOWN_STATUSES = frozenset(s for s, g in STATUS_GROUPS.items() if g == "unknown")

GROUP_LABELS = {
    "verifiable": "可核验",
    "negative": "有证据否定",
    "unknown": "未知（不得计为无效）",
}

# 同一证件有多份材料时，否定性证据优先，其次确认，最后待核验。
_STATUS_RANK = {
    "revoked": 0,
    "unmatched": 1,
    "not_authoritative": 2,
    "expired": 3,
    "confirmed": 4,
    "pending": 5,
}


@dataclass
class CredentialAssessment:
    credential: Credential
    status: str
    effective_check: Check | None
    is_expired: bool


@dataclass
class ClaimAssessment:
    claim: Claim
    status: str
    credential_assessments: list[CredentialAssessment] = field(default_factory=list)

    @property
    def evidence_credential(self) -> Credential | None:
        for item in self.credential_assessments:
            if item.credential is not None:
                return item.credential
        return None

    @property
    def effective_check(self) -> Check | None:
        chosen = self.chosen_assessment()
        return chosen.effective_check if chosen else None

    def chosen_assessment(self) -> CredentialAssessment | None:
        if not self.credential_assessments:
            return None
        return min(self.credential_assessments, key=lambda a: _STATUS_RANK[a.status])


def _is_check_effective(check: Check, on_date: date) -> bool:
    if check.valid_from is not None and check.valid_from > on_date:
        return False
    if check.valid_to is not None and check.valid_to < on_date:
        return False
    return True


def effective_checks(checks: list[Check], on_date: date) -> list[Check]:
    """返回在基准日当日处于生效区间内的核验记录，新记录在前。"""
    active = [c for c in checks if _is_check_effective(c, on_date)]
    # supersedes 指向旧记录：有新版本时旧版本不应再作为当日依据。
    superseded = {c.supersedes for c in active if c.supersedes}
    active = [c for c in active if c.check_id not in superseded]
    active.sort(
        key=lambda c: (
            c.valid_from or date.min,
            c.checked_at or date.min,
        ),
        reverse=True,
    )
    return active


def _assess_credential(
    credential: Credential,
    all_checks: list[Check],
    on_date: date,
    warnings: list[str],
) -> CredentialAssessment:
    is_expired = credential.expires_on is not None and credential.expires_on < on_date
    active = effective_checks(
        [c for c in all_checks if c.credential_ref == credential.credential_ref],
        on_date,
    )
    if len(active) > 1:
        warnings.append(
            f"证件 {credential.credential_ref} 在 {on_date} 存在 {len(active)} 条同日有效核验记录，"
            f"按生效时间取最新一条 {active[0].check_id}"
        )
    check = active[0] if active else None

    if check is None:
        # 已提交材料但核验尚未覆盖基准日：过期只作为附加事实，不把 pending 改判成无效。
        return CredentialAssessment(credential, "pending", None, is_expired)

    if check.result == "revoked":
        return CredentialAssessment(credential, "revoked", check, is_expired)
    if check.result == "unmatched":
        return CredentialAssessment(credential, "unmatched", check, is_expired)
    if check.result == "not_authoritative":
        return CredentialAssessment(credential, "not_authoritative", check, is_expired)
    if check.result == "issuer_confirmed":
        # 确认只在证件本身未过期时成立；过期压过确认，但不压过否定性核验结论。
        status = "expired" if is_expired else "confirmed"
        return CredentialAssessment(credential, status, check, is_expired)
    # loader 已限制受控取值，走到这里属于程序缺陷而非数据问题。
    raise ValueError(f"未处理的核验结果 {check.result}")


def assess_claim(
    claim: Claim,
    bundle: DataBundle,
    on_date: date,
    warnings: list[str],
) -> ClaimAssessment:
    person_credentials = [c for c in bundle.credentials if c.person_ref == claim.person_ref]
    if not person_credentials:
        return ClaimAssessment(claim=claim, status="no_credential")

    assessments = [
        _assess_credential(credential, bundle.checks, on_date, warnings)
        for credential in person_credentials
    ]
    chosen = min(assessments, key=lambda a: _STATUS_RANK[a.status])
    return ClaimAssessment(claim=claim, status=chosen.status, credential_assessments=assessments)


def is_account_active(active_from: date | None, active_to: date | None, on_date: date) -> bool:
    if active_from is not None and active_from > on_date:
        return False
    if active_to is not None and active_to < on_date:
        return False
    return True


def assess_scope(
    bundle: DataBundle,
    on_date: date,
    service_scope: str | None,
) -> tuple[list[ClaimAssessment], list[str]]:
    """筛选基准日活跃账号在指定服务范围内的声明并逐条判定。

    每条账号×服务声明独立进入统计；同一人跨账号/跨服务的声明不做合并。
    声明日期晚于基准日（声明尚未作出）的记录不进入当日分母。
    """
    warnings = list(bundle.warnings)
    active_accounts = {
        a.account_ref
        for a in bundle.accounts
        if is_account_active(a.active_from, a.active_to, on_date)
        and (service_scope is None or a.service_scope == service_scope)
    }

    in_scope: list[Claim] = []
    for claim in bundle.claims:
        if claim.account_ref not in active_accounts:
            continue
        if service_scope is not None and claim.service_scope != service_scope:
            continue
        if claim.claimed_at is not None and claim.claimed_at > on_date:
            continue
        in_scope.append(claim)

    results = [assess_claim(claim, bundle, on_date, warnings) for claim in in_scope]
    results.sort(key=lambda item: (_STATUS_RANK.get(item.status, 99), item.claim.claim_id))
    return results, warnings
