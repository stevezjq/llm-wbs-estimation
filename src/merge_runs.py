"""合并并行产生的 runs/part_*.jsonl 与 probe.jsonl，去重后写入 runs/runs.jsonl。"""
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"

# runs.jsonl 是合并后的权威记录（含 P5/P6 与 P0 重解析结果），必须优先于旧分片
files = [RUNS / "runs.jsonl"] + sorted(RUNS.glob("part_*.jsonl")) + [RUNS / "probe.jsonl"]
seen, recs = set(), []
for f in files:
    if not f.exists():
        continue
    for line in f.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r["run_id"] in seen:
            continue
        seen.add(r["run_id"])
        recs.append(r)

recs.sort(key=lambda r: (r["case_id"], r["prompt_id"], r["repeat"], r.get("stage", 1)))
out = RUNS / "runs.jsonl"
with open(out, "w", encoding="utf-8") as f:
    for r in recs:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

groups = Counter((r["case_id"], r["prompt_id"], r["repeat"]) for r in recs)
print(f"合并 {len(recs)} 条 -> {out}")
print("案例/提示词记录数:", dict(Counter((r["case_id"], r["prompt_id"]) for r in recs)))
print("组数:", len(groups), "| 每组样本:", dict(Counter(groups.values())))
print("解析失败:", sum(1 for r in recs if not r.get("parse_ok")),
      "| 修复调用:", sum(1 for r in recs if r.get("repair")))
print("模型:", Counter(r["model"] for r in recs))
