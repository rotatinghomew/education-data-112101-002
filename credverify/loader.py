"""读取 data/ 下的 CSV 登记资料并做结构性校验。

校验只针对结构和参照关系，不预设任何分析结论；无法识别的核验结果会
直接报错，避免未知状态被静默归入有效或无效。
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from .model import Account, Check, Claim, Credential, DataBundle

# checks.result 的受控取值；新增取值必须显式登记，未知取值报错而非猜测。
KNOWN_CHECK_RESULTS = frozenset(
    {
        "issuer_confirmed",
        "revoked",
        "unmatched",
        "not_authoritative",
    }
)


def _parse_iso_date(value: str) -> date | None:
    value = value.strip()
    if not value:
        return None
    return date.fromisoformat(value)


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise ValueError(f"文件 {path} 缺少表头")
        return [dict(row) for row in reader]


def _cell(row: dict[str, str], key: str, path: Path) -> str:
    if key not in row:
        raise ValueError(f"文件 {path.name} 缺少字段 {key}")
    return (row[key] or "").strip()


def load_bundle(data_dir: str | Path) -> DataBundle:
    data_dir = Path(data_dir)
    warnings: list[str] = []

    accounts: list[Account] = []
    for row in _read_rows(data_dir / "accounts.csv"):
        active_from = _parse_iso_date(_cell(row, "active_from", data_dir / "accounts.csv"))
        active_to = _parse_iso_date(_cell(row, "active_to", data_dir / "accounts.csv"))
        if active_from is not None and active_to is not None and active_to < active_from:
            raise ValueError(f"账号 {row['account_ref']} 的 active_to 早于 active_from")
        accounts.append(
            Account(
                account_ref=_cell(row, "account_ref", data_dir / "accounts.csv"),
                merchant_ref=_cell(row, "merchant_ref", data_dir / "accounts.csv"),
                active_from=active_from,
                active_to=active_to,
                service_scope=_cell(row, "service_scope", data_dir / "accounts.csv"),
            )
        )

    claims: list[Claim] = []
    for row in _read_rows(data_dir / "claims.csv"):
        claims.append(
            Claim(
                claim_id=row["claim_id"].strip(),
                person_ref=row["person_ref"].strip(),
                account_ref=row["account_ref"].strip(),
                merchant_ref=row["merchant_ref"].strip(),
                service_scope=row["service_scope"].strip(),
                title_text=row["title_text"].strip(),
                claimed_at=_parse_iso_date(row["claimed_at"]),
            )
        )

    credentials: list[Credential] = []
    for row in _read_rows(data_dir / "credentials.csv"):
        issued_on = _parse_iso_date(row["issued_on"])
        expires_on = _parse_iso_date(row["expires_on"])
        if issued_on is not None and expires_on is not None and expires_on < issued_on:
            raise ValueError(f"证件 {row['credential_ref']} 的 expires_on 早于 issued_on")
        credentials.append(
            Credential(
                credential_ref=row["credential_ref"].strip(),
                person_ref=row["person_ref"].strip(),
                issuer_ref=row["issuer_ref"].strip(),
                credential_name=row["credential_name"].strip(),
                issued_on=issued_on,
                expires_on=expires_on,
            )
        )

    checks: list[Check] = []
    for row in _read_rows(data_dir / "checks.csv"):
        result = row["result"].strip()
        if result not in KNOWN_CHECK_RESULTS:
            raise ValueError(
                f"核验记录 {row['check_id']} 的结果 {result!r} 不在受控取值内，"
                "需在 KNOWN_CHECK_RESULTS 中显式登记后才能分析"
            )
        valid_from = _parse_iso_date(row["valid_from"])
        valid_to = _parse_iso_date(row["valid_to"])
        if valid_from is not None and valid_to is not None and valid_to < valid_from:
            raise ValueError(f"核验记录 {row['check_id']} 的 valid_to 早于 valid_from")
        checks.append(
            Check(
                check_id=row["check_id"].strip(),
                credential_ref=row["credential_ref"].strip(),
                source_ref=row["source_ref"].strip(),
                checked_at=_parse_iso_date(row["checked_at"]),
                result=result,
                valid_from=valid_from,
                valid_to=valid_to,
                supersedes=row["supersedes"].strip() or None,
            )
        )

    _validate_references(accounts, claims, credentials, checks, warnings)
    return DataBundle(
        accounts=accounts,
        claims=claims,
        credentials=credentials,
        checks=checks,
        warnings=warnings,
    )


def _validate_references(
    accounts: list[Account],
    claims: list[Claim],
    credentials: list[Credential],
    checks: list[Check],
    warnings: list[str],
) -> None:
    account_ids = {a.account_ref for a in accounts}
    known_credential_ids = {c.credential_ref for c in credentials}

    def _ensure_unique(ids: set[str], ref: str, what: str) -> None:
        if ref in ids:
            raise ValueError(f"{what}标识 {ref} 重复")
        ids.add(ref)

    claim_ids: set[str] = set()
    check_ids: set[str] = set()
    seen_credential_ids: set[str] = set()
    for claim in claims:
        _ensure_unique(claim_ids, claim.claim_id, "声明")
        if claim.account_ref not in account_ids:
            raise ValueError(f"声明 {claim.claim_id} 引用了不存在的账号 {claim.account_ref}")
    for check in checks:
        _ensure_unique(check_ids, check.check_id, "核验记录")
        if check.credential_ref not in known_credential_ids:
            raise ValueError(f"核验记录 {check.check_id} 引用了不存在的证件 {check.credential_ref}")
        if check.supersedes is not None and check.supersedes not in {c.check_id for c in checks}:
            raise ValueError(f"核验记录 {check.check_id} 的 supersedes 指向不存在的记录")

    for credential in credentials:
        _ensure_unique(seen_credential_ids, credential.credential_ref, "证件")

    # 声明的服务范围应与所属账号登记一致；不一致是数据质量问题，给出告警而不是静默改写。
    account_by_id = {a.account_ref: a for a in accounts}
    for claim in claims:
        account = account_by_id.get(claim.account_ref)
        if account is not None and claim.service_scope != account.service_scope:
            warnings.append(
                f"声明 {claim.claim_id} 的服务范围 {claim.service_scope} "
                f"与账号 {claim.account_ref} 登记的 {account.service_scope} 不一致"
            )
