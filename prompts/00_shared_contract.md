# 输出契约 v1.1（P1–P6 共用）

## 1. 类别字典（category 必填其一）

| 代码 | 含义 |
|---|---|
| REQ | 需求澄清、范围确认、验收口径 |
| DES | 架构、数据模型、接口设计 |
| COD | 功能实现、脚本编写、兼容适配 |
| TEST | 功能/兼容/性能/回归测试、缺陷修复、试运行整改 |
| SEC | 审计留痕、越权与口令、安全测试/评审、合规材料 |
| DOC | 用户手册、接口文档、技术文档 |
| DEP | 环境准备、离线/容器化部署、监控与备份 |
| PM | 例会、进度跟踪、变更沟通、评审组织 |

## 2. 任务对象字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| task_id | string | 是 | 从 T01 起顺序编号，全局唯一 |
| name | string | 是 | 工作包名称 |
| category | string | 是 | 取类别字典代码 |
| deliverable | string | 是 | 可验收的交付物 |
| depends_on | string[] | 是 | 前置 task_id，无前置用空数组 |
| hours | number | 是 | 最可能工时，单位人时 |
| assumptions | string | 是 | 估算依据或关键假设，无则填 "—" |
| confidence | string | 是 | high / medium / low |

P4/P5/P6 额外字段：`optimistic`、`most_likely`、`pessimistic`（均为 number，且 `hours = most_likely`）。

## 3. 硬约束

1. `hours` 为净人工投入的最可能值，不含风险缓冲、管理缓冲与返工预留。
2. 常规工作包不超过 8 人时；确属不可再分的时间块（整段测试、离线部署、训练调参等）可至 10 人时，并在 `assumptions` 说明原因。
3. `depends_on` 只能引用本次输出中的 `task_id`，不得悬空、不得自引用、不得成环。
4. 只输出一个合法 JSON 对象 `{"tasks": [...]}`，不要输出解释性文字，不要使用 Markdown 代码围栏。
