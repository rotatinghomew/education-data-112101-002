"""资质声明可核验性分析工具包。

模块划分：
- model：登记记录的数据类
- loader：CSV 读取、日期解析与参照完整性校验
- evaluate：基准日时点的声明状态判定
- report：分母口径、汇总、明细与待复核名单
- snapshot：只追加的时点快照，保证已发布结果不被最新状态改写
"""

SNAPSHOT_FORMAT_VERSION = "1.0"
