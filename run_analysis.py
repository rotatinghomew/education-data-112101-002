"""命令行入口。

用法示例：
  python3 -I run_analysis.py report --baseline-date 2026-03-31
  python3 -I run_analysis.py report --baseline-date 2026-03-31 --scope counselling --json
  python3 -I run_analysis.py review --baseline-date 2026-03-31 --csv out/review.csv
  python3 -I run_analysis.py snapshot create --baseline-date 2026-03-31
  python3 -I run_analysis.py snapshot verify --file snapshots/snapshot_....json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

# 支持以 `python3 -I run_analysis.py` 方式运行：-I 会隔离脚本目录，
# 这里显式把脚本所在目录加入 sys.path 后再导入本项目模块。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from credverify.render import detail_fieldnames, render_review_list, render_text  # noqa: E402
from credverify.report import build_report_from_files  # noqa: E402
from credverify.snapshot import create_snapshot, verify_snapshot  # noqa: E402

DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_SNAPSHOTS_DIR = Path(__file__).resolve().parent / "snapshots"


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _write_detail_csv(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = detail_fieldnames()
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in report["details"]:
            writer.writerow({key: row.get(key, "") for key in fields})


def _write_review_csv(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(report["review_queue"][0].keys()) if report["review_queue"] else [
        "claim_id",
        "account_ref",
        "person_ref",
        "service_scope",
        "title_text",
        "status",
        "status_label",
        "reason",
        "evidence_note",
    ]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report["review_queue"])


def _add_common_date_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--baseline-date", required=True, type=_parse_date, help="基准日 ISO 8601，如 2026-03-31")
    parser.add_argument("--scope", default=None, help="服务范围过滤，如 parent_guidance/counselling/course_advice；缺省为全部")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="登记 CSV 所在目录")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="家庭教育/咨询头衔声明的可核验性时点分析")
    sub = parser.add_subparsers(dest="command", required=True)

    report_p = sub.add_parser("report", help="按基准日与服务范围输出汇总与明细")
    _add_common_date_args(report_p)
    report_p.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    report_p.add_argument("--detail-csv", type=Path, help="同时把全量明细写入指定 CSV")

    review_p = sub.add_parser("review", help="输出待复核名单（不含已确认声明）")
    _add_common_date_args(review_p)
    review_p.add_argument("--csv", type=Path, help="把待复核名单写入指定 CSV；缺省打印文本")

    snapshot_p = sub.add_parser("snapshot", help="创建或校验时点快照")
    snap_sub = snapshot_p.add_subparsers(dest="snapshot_command", required=True)

    create_p = snap_sub.add_parser("create", help="冻结当前分析结果与输入指纹")
    _add_common_date_args(create_p)
    create_p.add_argument("--snapshots-dir", default=str(DEFAULT_SNAPSHOTS_DIR))

    verify_p = snap_sub.add_parser("verify", help="比对快照输入与当前登记资料，判断结论是否可复现")
    verify_p.add_argument("--file", required=True, type=Path)
    verify_p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "report":
        report = build_report_from_files(args.data_dir, args.baseline_date, args.scope)
        if args.detail_csv:
            _write_detail_csv(report, args.detail_csv)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=False))
        else:
            print(render_text(report))
        return 0

    if args.command == "review":
        report = build_report_from_files(args.data_dir, args.baseline_date, args.scope)
        if args.csv:
            _write_review_csv(report, args.csv)
            print(f"待复核名单已写入 {args.csv}，共 {len(report['review_queue'])} 条")
        else:
            print(render_review_list(report))
        return 0

    if args.command == "snapshot":
        if args.snapshot_command == "create":
            path = create_snapshot(
                args.data_dir,
                args.snapshots_dir,
                args.baseline_date,
                args.scope,
            )
            print(f"已创建快照：{path}")
            return 0
        result = verify_snapshot(args.file, args.data_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["inputs_unchanged"] and result["report_reproducible_from_current_inputs"] else 2

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
