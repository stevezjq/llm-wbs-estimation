"""批量调用 LLM 生成 WBS 与工时估算。

用法:
    python src/run_batch.py            # 真实调用，需要 DEEPSEEK_API_KEY
    python src/run_batch.py --mock     # 离线 mock，用于验证流水线
    python src/run_batch.py --only-case A --only-prompt P1 --repeats 1   # 小样本试跑
"""
import argparse
import csv
import hashlib
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))

CASE_FILES = {c["id"]: c for c in CFG["cases"]}
PROMPTS = {p["id"]: p for p in CFG["prompts"]}
CONTRACT = (ROOT / "prompts/00_shared_contract.md").read_text(encoding="utf-8")

CAT_MAP = {"需求": "REQ", "设计": "DES", "编码": "COD", "测试": "TEST",
           "安全": "SEC", "文档": "DOC", "部署": "DEP", "管理": "PM"}

# mock 参数：提示词越强，遗漏越少、越接近真值
STRENGTH = {"P0": 0.0, "P1": 0.25, "P2": 0.55, "P3": 0.72, "P4": 0.85, "P5": 0.88, "P6": 0.9}
DROP = {"REQ": 0.20, "DES": 0.10, "COD": 0.08, "TEST": 0.30,
        "SEC": 0.50, "DOC": 0.35, "DEP": 0.30, "PM": 0.60}
MULT = {"REQ": 0.80, "DES": 0.90, "COD": 0.95, "TEST": 0.75,
        "SEC": 0.60, "DOC": 0.85, "DEP": 0.85, "PM": 0.75}


def render(tpl: str, mapping: dict) -> str:
    for k, v in mapping.items():
        tpl = tpl.replace("{{" + k + "}}", v)
    return tpl


def parse_json(text: str) -> dict:
    t = re.sub(r"^\s*```(?:json)?|```\s*$", "", text.strip(), flags=re.M).strip()
    i = t.find("{")
    if i < 0:
        raise ValueError("no json object")
    obj, _ = json.JSONDecoder().raw_decode(t[i:])
    if not isinstance(obj.get("tasks"), list):
        raise ValueError("missing tasks")
    for x in obj["tasks"]:
        x["hours"] = float(x.get("hours") or x.get("most_likely") or 0)
    return obj


SUMMARY_ROWS = {"合计", "总计", "小计", "汇总", "合计工时", "总工时"}


def _clean(cell: str) -> str:
    return cell.strip().strip("*_").strip()


def _is_summary_row(name: str) -> bool:
    bare = re.sub(r"[*_\s]", "", name)
    return not bare or bare in ("—", "-") or any(k in bare for k in SUMMARY_ROWS)


def parse_markdown_table(text: str) -> dict:
    tasks = []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 4 or set(cells[0]) <= set("-: "):
            continue
        if cells[0] in ("序号", "No", "#") or "工时" in cells[-1]:
            continue
        name, deliverable = _clean(cells[1]), _clean(cells[2])
        # 合计行、模块标题行、空行不是工作包
        if _is_summary_row(name) or not deliverable or deliverable in ("—", "-"):
            continue
        m = re.search(r"[\d.]+", cells[-1])
        if not m:
            continue
        tasks.append({"task_id": f"T{len(tasks)+1:02d}", "name": name,
                      "category": "UNK", "deliverable": deliverable,
                      "depends_on": [], "hours": float(m.group()),
                      "assumptions": "—", "confidence": "low"})
    if not tasks:
        raise ValueError("no markdown table rows")
    return {"tasks": tasks}


# ---------------- mock ----------------

def mock_tasks(case: str, prompt: str, rep: int) -> list[dict]:
    rng = random.Random(f"{case}|{prompt}|{rep}")
    path = ROOT / CASE_FILES[case]["expert_wbs"]
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    s = STRENGTH[prompt]
    out = []
    for r in rows:
        cat = CAT_MAP[r["category"]]
        p_drop = DROP[cat] * (1 - 0.85 * s) * (1 + 0.2 * (rng.random() - 0.5))
        if rng.random() < p_drop:
            continue
        h = float(r["hours"]) * (float(r["hours"]) / 8) ** -0.12 * MULT[cat] \
            * (0.92 + 0.08 * s) * rng.lognormvariate(0, 0.18)
        h = round(max(h, 0.5) * 2) / 2
        t = {"task_id": f"T{len(out)+1:02d}", "name": r["name"],
             "category": cat if prompt != "P0" else "UNK",
             "deliverable": r["deliverable"], "depends_on": [],
             "hours": h, "assumptions": "mock 生成", "confidence": "medium"}
        if prompt == "P4":
            t["optimistic"] = round(h * 0.7, 1)
            t["most_likely"] = h
            t["pessimistic"] = round(h * 1.8, 1)
        out.append(t)
    return out


def mock_raw(tasks: list[dict], prompt: str) -> str:
    if prompt == "P0":
        lines = ["| 序号 | 工作包 | 交付物 | 工时 |", "|---|---|---|---|"]
        for i, t in enumerate(tasks, 1):
            lines.append(f"| {i} | {t['name']} | {t['deliverable']} | {t['hours']} |")
        return "\n".join(lines)
    return json.dumps({"tasks": tasks}, ensure_ascii=False, indent=1)


def record(case, prompt, stage, rep, messages, raw, obj, model, temp=0.0,
           usage=None, latency=0, repair=False, mock=False):
    rid = hashlib.sha1(f"{model}|{case}|{prompt}|{rep}|{stage}".encode()).hexdigest()[:12]
    return {"run_id": rid, "case_id": case, "prompt_id": prompt, "stage": stage,
            "repeat": rep, "model": model, "temperature": temp,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "request_messages": messages, "raw_response": raw,
            "reasoning_content": "", "parsed": obj, "usage": usage or {},
            "latency_ms": latency, "parse_ok": obj is not None, "repair": repair,
            "mock": mock}


# ---------------- 真实调用 ----------------

def call_model(client, messages):
    kw = dict(model=CFG["model"]["name"], messages=messages,
              max_tokens=CFG["model"]["max_tokens"],
              timeout=CFG["model"]["timeout_s"])
    try:
        r = client.chat.completions.create(temperature=CFG["model"]["temperature"], **kw)
    except Exception as e:
        if "temperature" in str(e).lower():
            r = client.chat.completions.create(**kw)
        else:
            raise
    msg = r.choices[0].message
    return (msg.content or ""), (getattr(msg, "reasoning_content", "") or ""), \
        (r.usage.model_dump() if r.usage else {})


def run_real(case_ids, prompt_ids, repeats, out_path):
    from openai import OpenAI
    key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        sys.exit("未找到 DEEPSEEK_API_KEY / OPENAI_API_KEY，请设置后重试")
    client = OpenAI(api_key=key, base_url=CFG["model"]["base_url"])
    done = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(line)["run_id"])
            except Exception:
                pass
    total, n = 0, 0
    for case in case_ids:
        req = (ROOT / CASE_FILES[case]["requirement"]).read_text(encoding="utf-8")
        for pid in prompt_ids:
            stages = PROMPTS[pid]["stages"]
            for rep in range(repeats):
                total += len(stages)
                stage1_raw = None
                for si, tpl_path in enumerate(stages, 1):
                    tpl = (ROOT / tpl_path).read_text(encoding="utf-8")
                    mapping = {"REQUIREMENT": req, "CONTRACT": CONTRACT,
                               "STAGE1_JSON": stage1_raw or "{}"}
                    user = render(tpl, mapping)
                    messages = [{"role": "system", "content": CFG["run"]["system_prompt"]},
                                {"role": "user", "content": user}]
                    rid = hashlib.sha1(
                        f"{CFG['model']['name']}|{case}|{pid}|{rep}|{si}".encode()).hexdigest()[:12]
                    if rid in done:
                        print(f"skip {case} {pid} r{rep} s{si}")
                        continue
                    last_err = None
                    for attempt in range(CFG["run"]["max_retries"]):
                        try:
                            t0 = time.time()
                            raw, reasoning, usage = call_model(client, messages)
                            lat = int((time.time() - t0) * 1000)
                            try:
                                obj = parse_markdown_table(raw) if PROMPTS[pid]["format"] == "markdown_table" else parse_json(raw)
                            except Exception:
                                fix = messages + [{"role": "assistant", "content": raw},
                                                  {"role": "user", "content": "输出无法解析为 JSON。请严格只输出合法 JSON，不要任何解释或代码围栏。"}]
                                raw2, reasoning2, usage2 = call_model(client, fix)
                                obj = parse_json(raw2)
                                rec = record(case, pid, si, rep, messages,
                                             raw + "\n---REPAIR---\n" + raw2, obj,
                                             CFG["model"]["name"], CFG["model"]["temperature"],
                                             usage2, lat, repair=True)
                                append_jsonl(out_path, rec)
                                done.add(rid)
                                stage1_raw = raw2 if si == 1 else stage1_raw
                                n += 1
                                print(f"[{n}/{total}] {case} {pid} r{rep} s{si} ok(repair)")
                                last_err = None
                                break
                            rec = record(case, pid, si, rep, messages, raw, obj,
                                         CFG["model"]["name"], CFG["model"]["temperature"],
                                         usage, lat)
                            append_jsonl(out_path, rec)
                            done.add(rid)
                            stage1_raw = raw if si == 1 else stage1_raw
                            n += 1
                            print(f"[{n}/{total}] {case} {pid} r{rep} s{si} ok")
                            last_err = None
                            break
                        except Exception as e:
                            last_err = e
                            time.sleep(2 ** attempt)
                    if last_err is not None:
                        print(f"[ERR] {case} {pid} r{rep} s{si}: {last_err}")


def append_jsonl(path, obj):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def run_mock(case_ids, prompt_ids, repeats, out_path):
    with open(out_path, "w", encoding="utf-8") as f:
        for case in case_ids:
            req = (ROOT / CASE_FILES[case]["requirement"]).read_text(encoding="utf-8")
            for pid in prompt_ids:
                for rep in range(repeats):
                    tasks = mock_tasks(case, pid, rep)
                    raw = mock_raw(tasks, pid)
                    if pid == "P3":
                        s1 = [{k: t[k] for k in ("task_id", "name", "category",
                                                 "deliverable", "depends_on")} for t in tasks]
                        obj1 = {"tasks": s1, "assumptions": "mock"}
                        rec1 = record(case, pid, 1, rep, [], mock_raw(s1, "P1"), obj1,
                                      "mock", mock=True)
                        f.write(json.dumps(rec1, ensure_ascii=False) + "\n")
                        obj = {"tasks": tasks}
                        rec2 = record(case, pid, 2, rep, [], raw, obj, "mock", mock=True)
                        f.write(json.dumps(rec2, ensure_ascii=False) + "\n")
                    else:
                        obj = parse_markdown_table(raw) if pid == "P0" else {"tasks": tasks}
                        rec = record(case, pid, 1, rep, [], raw, obj, "mock", mock=True)
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"mock 写入 {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--only-case", default="ABC")
    ap.add_argument("--only-prompt", default="P0,P1,P2,P3,P4,P5,P6")
    ap.add_argument("--repeats", type=int, default=CFG["run"]["n_repeat"])
    ap.add_argument("--model", default=None, help="覆盖 config 中的模型名")
    ap.add_argument("--base-url", default=None, help="覆盖 config 中的 base_url")
    ap.add_argument("--out", default=None, help="自定义输出 JSONL（并行跑时分开写）")
    args = ap.parse_args()
    if args.model:
        CFG["model"]["name"] = args.model
    if args.base_url:
        CFG["model"]["base_url"] = args.base_url

    cases = [c for c in args.only_case if c in CASE_FILES]
    prompts = [p.strip() for p in args.only_prompt.split(",") if p.strip() in PROMPTS]
    out_dir = ROOT / CFG["run"]["out_dir"]
    out_dir.mkdir(exist_ok=True)
    out_path = Path(args.out) if args.out else ROOT / CFG["output"]["jsonl"]
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if args.mock:
        run_mock(cases, prompts, args.repeats, ROOT / "runs/runs_mock.jsonl")
    else:
        run_real(cases, prompts, args.repeats, out_path)


if __name__ == "__main__":
    main()
