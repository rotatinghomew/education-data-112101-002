"""时点快照：冻结已用于处置/发布的分析结果。

快照在生成时连同输入 CSV 的哈希一起写入，只追加、不就地改写。
重新生成报告会读取当前登记资料（反映补录/更正后的最新状态），
而历史快照仍保留当时依据的证据与结论；verify 子命令可比对当前输入
与快照输入的指纹，明确说明历史结论是否仍可被当前资料复现。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

from . import SNAPSHOT_FORMAT_VERSION
from .report import build_report_from_files

INPUT_FILES = ("accounts.csv", "claims.csv", "credentials.csv", "checks.csv")


def fingerprint_inputs(data_dir: str | Path) -> dict[str, str]:
    data_dir = Path(data_dir)
    fingerprints: dict[str, str] = {}
    for name in INPUT_FILES:
        path = data_dir / name
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        fingerprints[name] = f"sha256:{digest}"
    return fingerprints


def create_snapshot(
    data_dir: str | Path,
    snapshots_dir: str | Path,
    on_date: date,
    service_scope: str | None,
    now: datetime | None = None,
) -> Path:
    now = now or datetime.now(timezone.utc)
    generated_at = now.replace(microsecond=0).isoformat()
    report = build_report_from_files(data_dir, on_date, service_scope, generated_at)
    payload = {
        "snapshot_format_version": SNAPSHOT_FORMAT_VERSION,
        "snapshot_created_at": generated_at,
        "input_fingerprints": fingerprint_inputs(data_dir),
        "report": report,
    }

    snapshots_dir = Path(snapshots_dir)
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    scope_part = service_scope or "all_scopes"
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    path = snapshots_dir / f"snapshot_{on_date.isoformat()}_{scope_part}_{stamp}.json"
    if path.exists():  # 同一秒重复生成时避免覆盖既有快照
        suffix = 1
        while path.exists():
            path = snapshots_dir / (
                f"snapshot_{on_date.isoformat()}_{scope_part}_{stamp}_{suffix}.json"
            )
            suffix += 1
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def load_snapshot(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def verify_snapshot(path: str | Path, data_dir: str | Path) -> dict:
    """比对快照输入指纹与当前登记资料，并复核快照内部结论的自洽性。"""
    payload = load_snapshot(path)
    stored = payload.get("input_fingerprints", {})
    current = fingerprint_inputs(data_dir)

    changed = sorted(name for name in stored if stored.get(name) != current.get(name))
    missing = sorted(name for name in stored if name not in current)
    report = payload["report"]
    baseline = date.fromisoformat(report["parameters"]["baseline_date"])
    scope = report["parameters"]["service_scope"]
    recomputed = build_report_from_files(data_dir, baseline, scope)

    recomputed_cmp = json.dumps(
        {k: v for k, v in recomputed.items() if k != "generated_at"},
        ensure_ascii=False,
        sort_keys=True,
    )
    stored_cmp = json.dumps(
        {k: v for k, v in report.items() if k != "generated_at"},
        ensure_ascii=False,
        sort_keys=True,
    )
    return {
        "snapshot": str(path),
        "inputs_unchanged": not changed and not missing,
        "changed_inputs": changed,
        "missing_inputs": missing,
        "report_reproducible_from_current_inputs": recomputed_cmp == stored_cmp,
        "stored_summary": {
            "total_claims": report["denominator"]["total_claims"],
            "verifiable_ratio": report["verifiability"]["verifiable_ratio"],
            "counts": {row["status"]: row["count"] for row in report["status_breakdown"]},
        },
        "current_summary": {
            "total_claims": recomputed["denominator"]["total_claims"],
            "verifiable_ratio": recomputed["verifiability"]["verifiable_ratio"],
            "counts": {row["status"]: row["count"] for row in recomputed["status_breakdown"]},
        },
    }
