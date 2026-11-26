"""Re-parse stored P0 markdown responses with the fixed table parser (no API calls)."""
import json
import sys

from run_batch import ROOT, parse_markdown_table

RUNS = ROOT / "runs" / "runs.jsonl"


def main() -> int:
    records = [json.loads(line) for line in RUNS.open(encoding="utf-8") if line.strip()]
    changed = failed = 0
    for rec in records:
        if rec["prompt_id"] != "P0":
            continue
        before = rec.get("parsed", {}).get("tasks", [])
        try:
            parsed = parse_markdown_table(rec["raw_response"])
            ok = True
        except Exception as exc:  # noqa: BLE001
            parsed, ok = {"tasks": []}, False
            print(f"[FAIL] {rec['run_id']} {rec['case_id']} r{rec['repeat']}: {exc}")
            failed += 1
        after = parsed["tasks"]
        if len(before) != len(after) or sum(t["hours"] for t in before) != sum(t["hours"] for t in after):
            changed += 1
        rec["parsed"], rec["parse_ok"] = parsed, ok

    backup = RUNS.with_suffix(".jsonl.bak")
    backup.write_bytes(RUNS.read_bytes())
    tmp = RUNS.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    tmp.replace(RUNS)
    print(f"records={len(records)} changed={changed} failed={failed} backup={backup.name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
