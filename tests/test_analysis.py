"""分析逻辑测试：时点口径、状态分类、版本链、快照防改写。"""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from credverify.evaluate import (  # noqa: E402
    NEGATIVE_STATUSES,
    UNKNOWN_STATUSES,
    VERIFIABLE_STATUSES,
    assess_scope,
    effective_checks,
    is_account_active,
)
from credverify.loader import load_bundle  # noqa: E402
from credverify.report import build_report  # noqa: E402
from credverify.snapshot import create_snapshot, verify_snapshot  # noqa: E402

DATA_DIR = ROOT / "data"


def _status_by_claim(report: dict) -> dict[str, str]:
    return {row["claim_id"]: row["status"] for row in report["details"]}


class PointInTimeStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = load_bundle(DATA_DIR)

    def test_baseline_2026_03_31_full_breakdown(self) -> None:
        report = build_report(self.bundle, date(2026, 3, 31))
        statuses = _status_by_claim(report)
        counts = {row["status"]: row["count"] for row in report["status_breakdown"]}
        # 30 个账号全部活跃，30 条声明全部在基准日前作出。
        self.assertEqual(report["denominator"]["total_claims"], 30)
        self.assertEqual(report["denominator"]["active_accounts"], 30)
        self.assertEqual(sum(counts.values()), 30)
        # 七类状态的受控取值始终呈现；该基准日的实际分布如下。
        self.assertEqual(
            counts,
            {
                "confirmed": 7,
                "revoked": 6,
                "unmatched": 7,
                "not_authoritative": 9,
                "expired": 1,
                "pending": 0,
                "no_credential": 0,
            },
        )
        # 具体抽样：撤销、过期、非权威、无法匹配、确认各一。
        self.assertEqual(statuses["CL-7"], "revoked")
        self.assertEqual(statuses["CL-3"], "expired")  # CR-2 已过期且仅有确认记录
        self.assertEqual(statuses["CL-1"], "not_authoritative")  # 培训结业证明
        self.assertEqual(statuses["CL-6"], "unmatched")
        self.assertEqual(statuses["CL-5"], "confirmed")
        # pending 需要基准日早于核验生效日，另见 test_pending_before_check_effective。

    def test_pending_before_check_effective(self) -> None:
        # 2026-03-06：CH-5（CR-5）2026-03-07 才生效，CL-5 当日仍是 pending。
        report = build_report(self.bundle, date(2026, 3, 6))
        statuses = _status_by_claim(report)
        self.assertEqual(statuses["CL-5"], "pending")
        self.assertIn("pending", UNKNOWN_STATUSES)
        # 未知状态仍在分母中。
        self.assertEqual(
            report["denominator"]["total_claims"],
            len(report["details"]),
        )

    def test_revocation_supersession_chain(self) -> None:
        # CR-2：CH-2 确认（有效期至 2026-05-31），CH-3 自 2026-06-01 撤销并取代 CH-2。
        before = build_report(self.bundle, date(2026, 5, 31))
        self.assertEqual(_status_by_claim(before)["CL-3"], "expired")  # 证件 2025-06-01 已过期
        after = build_report(self.bundle, date(2026, 6, 15))
        self.assertEqual(_status_by_claim(after)["CL-3"], "revoked")
        checks = effective_checks(
            [c for c in self.bundle.checks if c.credential_ref == "CR-2"],
            date(2026, 6, 15),
        )
        self.assertEqual([c.check_id for c in checks], ["CH-3"])

    def test_negative_check_outranks_expiry_but_flag_is_kept(self) -> None:
        # CR-24 过期日 2026-01-15，CH-24 为 not_authoritative：否定性核验优先展示，
        # 但过期事实作为附加标志保留在明细中，证据链可同时看到两者。
        report = build_report(self.bundle, date(2026, 3, 31))
        row = next(r for r in report["details"] if r["claim_id"] == "CL-24")
        self.assertEqual(row["status"], "not_authoritative")
        self.assertEqual(row["check_result"], "not_authoritative")
        self.assertEqual(row["credential_expired_on_date"], "True")

    def test_expired_outranks_confirmation(self) -> None:
        # CR-2 已过期，CH-2 为 issuer_confirmed：过期压过确认。
        report = build_report(self.bundle, date(2026, 5, 31))
        row = next(r for r in report["details"] if r["claim_id"] == "CL-3")
        self.assertEqual(row["status"], "expired")
        self.assertEqual(row["check_result"], "issuer_confirmed")
        self.assertEqual(row["credential_expired_on_date"], "True")

    def test_unknown_is_never_counted_as_invalid(self) -> None:
        report = build_report(self.bundle, date(2026, 3, 31))
        ver = report["verifiability"]
        total = report["denominator"]["total_claims"]
        self.assertEqual(
            ver["numerator_claims"] + ver["negative_claims"] + ver["unknown_claims"],
            total,
        )
        self.assertEqual(set(VERIFIABLE_STATUSES), {"confirmed"})
        self.assertTrue(NEGATIVE_STATUSES.isdisjoint(UNKNOWN_STATUSES))

    def test_verifiable_ratio_denominator_includes_all_statuses(self) -> None:
        report = build_report(self.bundle, date(2026, 3, 31))
        self.assertEqual(report["verifiability"]["verifiable_ratio"], round(7 / 30, 6))
        for row in report["status_breakdown"]:
            if row["count"]:
                self.assertIsNotNone(row["share_of_denominator"])


class ScopeAndActivityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = load_bundle(DATA_DIR)

    def test_service_scope_filter(self) -> None:
        report = build_report(self.bundle, date(2026, 3, 31), "counselling")
        self.assertTrue(report["details"])
        self.assertTrue(all(row["service_scope"] == "counselling" for row in report["details"]))
        scopes = {row["service_scope"] for row in report["details"]}
        self.assertEqual(scopes, {"counselling"})

    def test_same_person_claims_are_not_merged(self) -> None:
        # P-1 在 A-1（parent_guidance）与 A-2（counselling）各有一条独立声明。
        report = build_report(self.bundle, date(2026, 3, 31))
        p1_rows = [row for row in report["details"] if row["person_ref"] == "P-1"]
        self.assertEqual(len(p1_rows), 2)
        self.assertEqual({row["claim_id"] for row in p1_rows}, {"CL-1", "CL-2"})
        counselling = build_report(self.bundle, date(2026, 3, 31), "counselling")
        self.assertEqual(
            [row["claim_id"] for row in counselling["details"] if row["person_ref"] == "P-1"],
            ["CL-2"],
        )

    def test_future_claim_excluded_from_denominator(self) -> None:
        # 基准日 2026-01-02 当天只有 CL-1 已作出（其余声明日期更晚）。
        report = build_report(self.bundle, date(2026, 1, 2))
        self.assertEqual([row["claim_id"] for row in report["details"]], ["CL-1"])

    def test_activity_window_logic(self) -> None:
        self.assertTrue(is_account_active(date(2025, 1, 1), None, date(2026, 3, 31)))
        self.assertFalse(is_account_active(date(2026, 5, 1), None, date(2026, 3, 31)))
        self.assertTrue(is_account_active(None, date(2026, 3, 31), date(2026, 3, 31)))
        self.assertFalse(is_account_active(None, date(2026, 3, 30), date(2026, 3, 31)))

    def test_every_detail_row_carries_evidence_fields(self) -> None:
        report = build_report(self.bundle, date(2026, 3, 31))
        for row in report["details"]:
            self.assertTrue(row["account_ref"] and row["person_ref"] and row["evidence_note"])
            if row["status"] != "no_credential":
                self.assertTrue(row["credential_ref"])


class SnapshotImmutabilityTests(unittest.TestCase):
    def test_snapshot_freezes_result_and_detects_later_correction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            work_data = tmp_path / "data"
            snapshots = tmp_path / "snapshots"
            work_data.mkdir()
            for name in ("accounts.csv", "claims.csv", "credentials.csv", "checks.csv"):
                (work_data / name).write_text((DATA_DIR / name).read_text(encoding="utf-8"), encoding="utf-8")

            baseline = date(2026, 3, 31)
            snapshot_path = create_snapshot(work_data, snapshots, baseline, None)
            payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
            frozen_ratio = payload["report"]["verifiability"]["verifiable_ratio"]
            self.assertEqual(frozen_ratio, round(7 / 30, 6))

            # 未改动时校验通过且结论可复现。
            ok = verify_snapshot(snapshot_path, work_data)
            self.assertTrue(ok["inputs_unchanged"])
            self.assertTrue(ok["report_reproducible_from_current_inputs"])

            # 模拟补录：为 CR-1 补一条自基准日起生效的 issuer_confirmed 核验。
            checks_path = work_data / "checks.csv"
            with checks_path.open("a", encoding="utf-8", newline="") as stream:
                stream.write(
                    "CH-31,CR-1,SRC-2,2026-03-30,issuer_confirmed,2026-03-30,,\n"
                )
            bundle = load_bundle(work_data)
            updated = build_report(bundle, baseline)
            # 补录在基准日已生效：当前分析中 CL-1 由 not_authoritative 转为 confirmed，
            # 待复核名单与可核验比例随之变化。
            self.assertEqual(_status_by_claim(updated)["CL-1"], "confirmed")
            self.assertGreater(
                updated["verifiability"]["verifiable_ratio"],
                frozen_ratio,
            )

            # 历史快照内容未被改写，且 verify 明确指出输入已变化、结论不可由当前输入复现。
            payload_after = json.loads(snapshot_path.read_text(encoding="utf-8"))
            self.assertEqual(
                payload_after["report"]["verifiability"]["verifiable_ratio"],
                frozen_ratio,
            )
            changed = verify_snapshot(snapshot_path, work_data)
            self.assertFalse(changed["inputs_unchanged"])
            self.assertEqual(changed["changed_inputs"], ["checks.csv"])
            self.assertFalse(changed["report_reproducible_from_current_inputs"])

    def test_snapshot_files_are_valid_json_with_fingerprints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = create_snapshot(DATA_DIR, Path(tmp), date(2026, 3, 31), "counselling")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(set(payload["input_fingerprints"]), {
                "accounts.csv",
                "claims.csv",
                "credentials.csv",
                "checks.csv",
            })
            self.assertEqual(
                payload["report"]["parameters"]["service_scope"],
                "counselling",
            )


class FixtureReadabilityTests(unittest.TestCase):
    def test_bundle_loads_without_unknown_results(self) -> None:
        bundle = load_bundle(DATA_DIR)
        self.assertEqual(len(bundle.accounts), 30)
        self.assertEqual(len(bundle.claims), 30)
        self.assertEqual(len(bundle.credentials), 29)  # CR-3 缺号是合成数据的正常现象
        self.assertEqual(len(bundle.checks), 30)


if __name__ == "__main__":
    unittest.main()
