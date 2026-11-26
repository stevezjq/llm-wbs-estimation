# LLM 辅助需求拆解与工时估算的误差分析

课程作业（《软件工程管理》2026 题目 2）的实验工程。三个需求案例 × 七组提示词 × 五次重复，对比 LLM 估算与专家真值。

## 1. 目录

```
data/requirements/     三份需求规格（A 设备预约 / B 漏洞扫描器 / C MiniMind 问答 MVP）
data/checklist/        埋点清单（各 15 项）+ 命中关键词 keywords.yaml
data/expert_wbs/       专家真值 WBS（v1.0，A 132h / B 160h / C 128h）+ estimated_basis.md
prompts/               P0–P6 提示词与共用输出契约（契约 v1.1）
config.yaml            模型、重复次数、案例与提示词矩阵
src/run_batch.py       批量调用（支持 --mock）
src/merge_runs.py      合并并行分片、去重
src/reparse_p0.py      用修正后的表格解析器重解析已存 P0 原文（不调 API）
src/analyze.py         指标、图表、偏差分解、基线与泛化分析
runs/                  原始 JSONL 记录（交付物"实验记录"）
analysis/              误差表、图与 summary.md；analysis/_mock 为流水线自检
```

提示词阶梯：P0 朴素 Markdown 表 → P1 结构化 JSON → P2 多角色强制清单 → P3 两阶段（先拆解再估时）→ P4 三点估算 → P5 交付物对账 → P6 交付物对账 + 隐性工作保留（最终框架）。

## 2. 环境

```bash
python -m venv --system-site-packages .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

## 3. 运行

模型固定为 `deepseek-chat`（`deepseek-flash` 是推理模型，正文常为空且忽略 temperature，不适合重复实验）。

先小样本验证端点（1 次调用）：

```bash
set DEEPSEEK_API_KEY=sk-xxx          # PowerShell: $env:DEEPSEEK_API_KEY="sk-xxx"
.venv/Scripts/python.exe src/run_batch.py --only-case A --only-prompt P0 --repeats 1
```

全量（3 案例 × 7 提示词 × 5 次 = 105 组；P3 两阶段，共 120 次调用）：

```bash
.venv/Scripts/python.exe src/run_batch.py
.venv/Scripts/python.exe src/analyze.py
```

若 P0 解析规则更新，可只重解析已存原文，避免重调 API：

```bash
.venv/Scripts/python.exe src/reparse_p0.py
.venv/Scripts/python.exe src/analyze.py
```

无 key 时先跑通链路（不代表实验结果）：

```bash
.venv/Scripts/python.exe src/run_batch.py --mock
.venv/Scripts/python.exe src/analyze.py --mock
```

## 4. 指标

| 指标 | 含义 |
|---|---|
| 总量带符号误差 | (LLM−专家)/专家，正为高估、负为低估 |
| APE / CV | 绝对百分比误差；5 次重复的变异系数（稳定性） |
| 偏差来源分解 | 总量比 = 任务数比 × 单任务工时比，判断高估来自“拆得细”还是“估得高” |
| 类别层误差 | 按 8 个类别汇总后比较，避免任务粒度差异污染单项误差 |
| 埋点要点提及率 | 15×3 个应识别工作项被关键词命中的比例；是覆盖上界，不等于单列工作包 |
| 三点区间覆盖率 | 真值落入 [Σa, Σb] 的比例与区间宽度；P4/P5/P6 可算 |
| 基线敏感性 | 真值整体 ×1.0/1.25/1.5 后重算偏差，检验结论是否依赖基线口径 |

**简化说明**：任务级配对主观性太强（LLM 拆 40 项、专家 20 项），统计改为**类别层汇总 + 埋点层提及 + 总量/结构分解**。

## 5. 注意

- 提示词 v1.0 已冻结；P5/P6 是在首批错误模式诊断后新增的，并在同一组案例上原样验证，属于 post-hoc 改进，报告需说明缺少留出案例。
- 禁止把专家真值、总量、类别占比写进提示词，否则构成信息泄漏。
- P0 的 Markdown 表含“合计/模块标题/空交付物”行，解析器已过滤；修正前后的总量差异见 `runs/runs.jsonl.bak` 与重解析记录。
- 埋点提及率用关键词初筛，正式引用前需人工抽检；同一要点可能被测试/文档任务顺带提及。
- 专家真值是“单熟手、净人工、无缓冲”的偏紧基线，绝对偏差结论需结合第 8 节敏感性分析解读。
- `analysis/_mock/` 仅用于验证流水线，禁止写入报告。
- CSV 均为 UTF-8（含 BOM）；用 Excel 另存时须保留该编码。
