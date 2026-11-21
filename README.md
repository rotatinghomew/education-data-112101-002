# 教育指导资质声明的覆盖率与时效分析

本地资料用于理解指导服务资质声明覆盖分析所涉及的记录关系。文件均为合成数据，标识符不对应真实个人、机构或账号。

## 目录

- `data/`：UTF-8 编码、首行为字段名的逗号分隔资料。空字段表示资料未提供，不代表数值为零或业务状态已否定。
- `credverify/`：分析逻辑（加载、时点判定、报告、快照），仅使用 Python 标准库。
- `run_analysis.py`：命令行入口。
- `tests/`：资料结构检查与分析逻辑测试，不预设业务输入之外的结果。

日期使用 ISO 8601 写法；时间字段为空时表示该记录没有提供时间。金额和计数字段以文本保存，具体解释须结合字段名及记录上下文。

## 运行与检查

需要 Python 3.11 或更高版本，无第三方运行依赖。建议以隔离模式运行，避免工作目录中的同名模块被导入：

```bash
python3 -I -m unittest discover -s tests          # 全部测试
python3 -I run_analysis.py report --baseline-date 2026-03-31
```

## 统计口径

### 分母（可核验比例的分母由哪些声明构成）

给定基准日与服务范围，分母是满足以下全部条件的**账号×服务声明**，逐条计数：

1. 所属账号在基准日当日活跃：`active_from <= 基准日`，且 `active_to` 为空或 `>= 基准日`；
2. 声明的服务范围与过滤条件匹配（不过滤时取全部服务范围）；
3. 声明日期 `claimed_at` 不晚于基准日（基准日之后才作出的声明不进入当日统计）。

同一个人跨账号、跨服务的声明**不合并**。例如 P-1 在 A-1 的家庭教育指导声明与 A-2 的心理咨询声明各占分母一条。未知状态（待核验、未提交证件）保留在分母中，不剔除，也不计为无效。

### 七类互斥状态

| 状态码 | 中文标签 | 分组 | 判定要点 |
| --- | --- | --- | --- |
| `confirmed` | 已确认（可核验） | 可核验 | 证件基准日未过期，且当日有效核验为 `issuer_confirmed` |
| `revoked` | 已撤销 | 有证据否定 | 当日有效核验为 `revoked` |
| `unmatched` | 无法匹配证件 | 有证据否定 | 当日有效核验为 `unmatched`（如证件属于其他持证人） |
| `not_authoritative` | 依据不具权威性 | 有证据否定 | 当日有效核验为 `not_authoritative`（典型为培训/结业记录，不构成头衔依据） |
| `expired` | 已过期 | 有证据否定 | 证件 `expires_on` 早于基准日，且当日核验为确认或无否定性核验 |
| `pending` | 尚未完成核验 | 未知 | 已提交证件，但没有任何在基准日生效的核验记录 |
| `no_credential` | 未提交证件 | 未知 | 声明人名下无任何证件材料 |

判定优先级：否定性核验结论（revoked / unmatched / not_authoritative）优先于证件过期；过期只压过 `issuer_confirmed`。过期事实同时以 `credential_expired_on_date` 标志保留在明细中，不会因归入否定类而丢失。核验记录按 `valid_from`/`valid_to` 选取基准日当日有效版本，`supersedes` 指向的旧版本在新版本生效后不再作为当日依据；证件 `expires_on` 当日仍有效、次日起过期。`checks.result` 出现未登记取值时加载直接报错，避免未知结果被静默归入有效或无效。

可核验比例 = `confirmed` 声明数 / 分母总数。汇总中的每一行都可经明细下钻到声明、所属账号、人员、证件、核验来源及当日状态。

## 命令

```bash
# 文本报告（含口径、七类汇总、比例、全量明细、待复核名单）
python3 -I run_analysis.py report --baseline-date 2026-03-31

# 限定服务范围；机器可读 JSON；同时导出全量明细 CSV
python3 -I run_analysis.py report --baseline-date 2026-03-31 \
    --scope counselling --json --detail-csv out/detail.csv

# 待复核名单（不含已确认），可导出 CSV
python3 -I run_analysis.py review --baseline-date 2026-03-31 --csv out/review.csv

# 时点快照：冻结报告与四个输入 CSV 的 SHA-256 指纹
python3 -I run_analysis.py snapshot create --baseline-date 2026-03-31

# 校验快照：比对输入指纹，并用当前登记重算，判断历史结论是否仍可复现
# 输入未变且可复现退出码 0；否则退出码 2
python3 -I run_analysis.py snapshot verify --file snapshots/snapshot_....json
```

## 补录更正与历史结论的保留

- `report`/`review` 每次都从当前登记资料重算：补录证件或更正核验后，当前分析与待复核名单立即变化。
- `snapshot create` 把报告全文连同输入文件指纹写入 `snapshots/`（只追加、不就地改写）。已用于处置或发布的结果以快照为准，后续更正不会改写快照中的证据与结论。
- `snapshot verify` 同时给出快照内汇总与按当前资料重算的汇总，并指出哪些输入文件发生了变化，使"当时依据了哪些证据"始终可说明。

## 数据文件

- `data/accounts.csv`：账号、商家、活跃区间与服务范围
- `data/claims.csv`：账号×服务×人员的头衔声明
- `data/credentials.csv`：证件/培训材料及有效期
- `data/checks.csv`：核验记录、生效区间与取代关系（`supersedes`）
