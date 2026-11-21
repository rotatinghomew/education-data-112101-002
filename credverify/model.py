"""登记资料的领域模型。空字段统一解析为 None，绝不解释成零或否定状态。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Account:
    account_ref: str
    merchant_ref: str
    active_from: date | None
    active_to: date | None
    service_scope: str


@dataclass(frozen=True)
class Claim:
    claim_id: str
    person_ref: str
    account_ref: str
    merchant_ref: str
    service_scope: str
    title_text: str
    claimed_at: date | None


@dataclass(frozen=True)
class Credential:
    credential_ref: str
    person_ref: str
    issuer_ref: str
    credential_name: str
    issued_on: date | None
    expires_on: date | None


@dataclass(frozen=True)
class Check:
    check_id: str
    credential_ref: str
    source_ref: str
    checked_at: date | None
    result: str
    valid_from: date | None
    valid_to: date | None
    supersedes: str | None


@dataclass
class DataBundle:
    accounts: list[Account]
    claims: list[Claim]
    credentials: list[Credential]
    checks: list[Check]
    warnings: list[str]
