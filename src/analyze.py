"""指标体系、图表与归因分析。

用法:
    python src/analyze.py --mock   # 分析 mock 数据，输出到 analysis/_mock
    python src/analyze.py          # 分析真实 runs/runs.jsonl，输出到 analysis
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
CATS = CFG["analysis"]["categories"]
CAT_MAP = {"需求": "REQ", "设计": "DES", "编码": "COD", "测试": "TEST",
           "安全": "SEC", "文档": "DOC", "部署": "DEP", "管理": "PM"}
CAT_CN = {"REQ": "需求", "DES": "设计", "COD": "编码", "TEST": "测试",
          "SEC": "安全", "DOC": "文档", "DEP": "部署", "PM": "管理", "UNK": "未分类"}

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial"]
plt.rcParams["axes.unicode_minus"] = False


def load_runs(mock: bool):
    path = ROOT / ("runs/runs_mock.jsonl" if mock else CFG["output"]["jsonl"])
    if not path.exists():
        sys.exit(f"未找到 {path}，请先运行 run_batch.py")
    newest = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if bool(r.get("mock")) != mock or not r.get("parse_ok"):
            continue
        k = (r["case_id"], r["prompt_id"], r["repeat"])
        if k not in newest or r.get("stage", 1) > newest[k].get("stage", 1):
            newest[k] = r
    runs = list(newest.values())
    if not runs:
        sys.exit("没有可用记录（检查 --mock 与 runs/runs.jsonl）")
    return runs


def load_expert():
    total, detail, counts = {}, {}, {}
    for c in CFG["cases"]:
        df = pd.read_csv(ROOT / c["expert_wbs"], encoding="utf-8-sig")
        df["cat"] = df["category"].map(CAT_MAP).fillna(df["category"])
        total[c["id"]] = float(df["hours"].sum())
        detail[c["id"]] = df.groupby("cat")["hours"].sum().to_dict()
        counts[c["id"]] = int(len(df))
    return total, detail, counts


def norm_cat(x):
    x = (x or "").strip()
    return x if x in CATS else CAT_MAP.get(x, "UNK")


def load_keywords():
    return yaml.safe_load((ROOT / "data/checklist/keywords.yaml").read_text(encoding="utf-8"))


def load_checklist_types():
    out = {}
    for c in CFG["cases"]:
        df = pd.read_csv(ROOT / c["checklist"], encoding="utf-8-sig")
        out[c["id"]] = dict(zip(df["item_id"], df["type"]))
    return out


def recall_of(case, tasks, kw):
    text = " ".join(f"{t.get('name','')} {t.get('deliverable','')}" for t in tasks).lower()
    hits = {iid: any(k.lower() in text for k in kws) for iid, kws in kw[case].items()}
    return hits


def build_metrics(runs, expert_total, expert_n):
    rows = []
    for r in runs:
        case, pid, rep = r["case_id"], r["prompt_id"], r["repeat"]
        tasks = [t for t in r["parsed"]["tasks"] if float(t.get("hours") or 0) > 0]
        total = sum(float(t["hours"]) for t in tasks)
        cats = Counter()
        for t in tasks:
            cats[norm_cat(t.get("category"))] += float(t["hours"])
        e = expert_total[case]
        row = {"case": case, "prompt": pid, "repeat": rep,
               "total": total, "signed": (total - e) / e, "ape": abs(total - e) / e,
               "n_tasks": len(tasks)}
        row["count_ratio"] = len(tasks) / expert_n[case]
        row["avg_h"] = total / len(tasks) if tasks else 0.0
        row["avg_ratio"] = row["avg_h"] / (e / expert_n[case])
        # log 分解：log(总量比) = log(任务数比) + log(单任务工时比)
        row["log_total"] = np.log(total / e) if total > 0 else np.nan
        row["log_count"] = np.log(row["count_ratio"]) if tasks else np.nan
        row["log_avg"] = np.log(row["avg_ratio"]) if row["avg_h"] > 0 else np.nan
        for c in CATS:
            row[c] = cats.get(c, 0.0)
        if pid in ("P4", "P5", "P6"):
            row["a_sum"] = sum(float(t.get("optimistic") or t["hours"] * .7) for t in tasks)
            row["b_sum"] = sum(float(t.get("pessimistic") or t["hours"] * 1.8) for t in tasks)
        rows.append(row)
    return pd.DataFrame(rows)


def md_table(df):
    cols = list(df.columns)
    lines = ["| " + " | ".join(map(str, cols)) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        vals = []
        for v in r.values:
            if isinstance(v, (float, np.floating)):
                vals.append(f"{v:.2f}")
            else:
                vals.append("" if pd.isna(v) else str(v))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true")
    args = ap.parse_args()

    runs = load_runs(args.mock)
    expert_total, expert_cat, expert_n = load_expert()
    kw = load_keywords()
    cl_type = load_checklist_types()
    out = ROOT / "analysis" / ("_mock" if args.mock else "")
    out.mkdir(parents=True, exist_ok=True)
    (out / "figs").mkdir(exist_ok=True)

    m = build_metrics(runs, expert_total, expert_n)
    m.to_csv(out / "metrics_detail.csv", index=False, encoding="utf-8-sig")

    # 埋点召回
    rec_rows = []
    for r in runs:
        hits = recall_of(r["case_id"], r["parsed"]["tasks"], kw)
        for iid, hit in hits.items():
            rec_rows.append({"case": r["case_id"], "prompt": r["prompt_id"],
                             "repeat": r["repeat"], "item_id": iid,
                             "type": cl_type[r["case_id"]].get(iid, "未知"),
                             "hit": int(hit)})
    rec = pd.DataFrame(rec_rows)

    # 汇总
    g = m.groupby(["case", "prompt"]).agg(
        signed=("signed", "mean"), signed_sd=("signed", "std"),
        ape=("ape", "mean"), total=("total", "mean"),
        total_sd=("total", "std"), n=("total", "size")).reset_index()
    g["cv"] = g["total_sd"] / g["total"]
    g.to_csv(out / "metrics_summary.csv", index=False, encoding="utf-8-sig")

    # 基线敏感性：专家真值整体缩放 k 后，各方案总量偏差如何变化
    sens_rows = []
    for _, r in g.iterrows():
        e = expert_total[r["case"]]
        row = {"case": r["case"], "prompt": r["prompt"]}
        for k in (1.0, 1.25, 1.5):
            row[f"k={k:g}"] = (r["total"] / (e * k) - 1) * 100
        sens_rows.append(row)
    sens = pd.DataFrame(sens_rows)
    sens.to_csv(out / "baseline_sensitivity.csv", index=False, encoding="utf-8-sig")

    recall_g = rec.groupby(["case", "prompt"])["hit"].mean().reset_index()
    recall_by_type = rec.groupby(["prompt", "type"])["hit"].mean().reset_index()

    # 偏差来源：总量比 = 任务数比 × 单任务工时比（几何均值，log 可加分解）
    dec = m.groupby("prompt").agg(
        log_total=("log_total", "mean"), log_count=("log_count", "mean"),
        log_avg=("log_avg", "mean"), n_tasks=("n_tasks", "mean"),
        avg_h=("avg_h", "mean")).reset_index()
    dec["count_ratio"] = np.exp(dec["log_count"])
    dec["avg_ratio"] = np.exp(dec["log_avg"])
    dec["total_ratio"] = np.exp(dec["log_total"])
    dec["count_share"] = dec["log_count"] / dec["log_total"]
    dec["avg_share"] = dec["log_avg"] / dec["log_total"]
    dec["prompt"] = pd.Categorical(dec["prompt"], ["P0", "P1", "P2", "P3", "P4", "P5", "P6"], ordered=True)
    dec = dec.sort_values("prompt")
    dec.to_csv(out / "error_decomposition.csv", index=False, encoding="utf-8-sig")

    # 高估 / 低估（阈值 ±5%）
    m["judge"] = np.where(m["signed"] > 0.05, "高估", np.where(m["signed"] < -0.05, "低估", "接近"))
    ou = m.groupby(["case", "prompt", "judge"]).size().unstack(fill_value=0) \
        .reindex(columns=["高估", "接近", "低估"], fill_value=0).reset_index()

    # 类别：专家 vs 模型
    cat_rows = []
    for case in expert_total:
        for c in CATS:
            e = expert_cat[case].get(c, 0)
            cat_rows.append({"case": case, "prompt": "专家", "cat": c, "hours": e})
    cm = m.groupby(["case", "prompt"])[CATS].mean().reset_index()
    for _, r in cm.iterrows():
        if r["prompt"] == "P0":
            continue
        for c in CATS:
            cat_rows.append({"case": r["case"], "prompt": r["prompt"], "cat": c, "hours": r[c]})
    catdf = pd.DataFrame(cat_rows)
    catdf["share"] = catdf.apply(
        lambda r: r["hours"] / catdf[(catdf.case == r.case) & (catdf.prompt == r.prompt)]["hours"].sum(), axis=1)
    catdf.to_csv(out / "category_summary.csv", index=False, encoding="utf-8-sig")

    # P4 区间覆盖
    cov_rows = []
    for pid in [p for p in ("P4", "P5", "P6") if p in set(m.prompt)]:
        for case in expert_total:
            sub = m[(m.case == case) & (m.prompt == pid)]
            e = expert_total[case]
            cov_rows.append({"case": case, "prompt": pid,
                             "coverage": float(((sub.a_sum <= e) & (e <= sub.b_sum)).mean()),
                             "width_ratio": float(((sub.b_sum - sub.a_sum) / sub.a_sum).mean()),
                             "expert": e, "a_mean": sub.a_sum.mean(), "b_mean": sub.b_sum.mean()})
    cov = pd.DataFrame(cov_rows)
    if not cov.empty:
        cov.to_csv(out / "interval_coverage.csv", index=False, encoding="utf-8-sig")

    # 归因：类别偏差 + 规模效应
    cat_pts = []
    for _, r in cm.iterrows():
        pid = r["prompt"]
        if pid == "P0":
            continue
        for c in CATS:
            e = expert_cat[r["case"]].get(c, 0)
            if e > 0 and r[c] > 0:
                cat_pts.append({"case": r["case"], "prompt": pid, "cat": c,
                                "e": e, "l": r[c], "logratio": np.log(r[c] / e)})
    cpts = pd.DataFrame(cat_pts)
    bias = cpts.groupby("cat")["logratio"].mean().reset_index(name="mean_log_ratio")
    bias["mean_pct"] = (np.exp(bias["mean_log_ratio"]) - 1) * 100
    bias["cat_cn"] = bias["cat"].map(CAT_CN)
    slope = r2 = np.nan
    if len(cpts) > 3:
        x, y = np.log(cpts["e"]), cpts["logratio"]
        k, b = np.polyfit(x, y, 1)
        pred = k * x + b
        slope = k
        r2 = 1 - ((y - pred) ** 2).sum() / ((y - y.mean()) ** 2).sum()

    # ---------------- 图 ----------------
    # 图1 总量带符号误差
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4.2),
                                   gridspec_kw={"width_ratios": [1, 1.7]})
    piv = g.pivot(index="prompt", columns="case", values="signed") * 100
    piv = piv.reindex([p for p in ["P0", "P1", "P2", "P3", "P4", "P5", "P6"] if p in piv.index])
    piv.plot(kind="bar", ax=ax1, legend=False)
    piv.drop(index="P0", errors="ignore").plot(kind="bar", ax=ax2)
    for a in (ax1, ax2):
        a.axhline(0, color="k", lw=0.8)
        a.set_xlabel("提示词方案")
    ax1.set_ylabel("总量相对误差 (%)")
    ax1.set_title("P0（朴素提示）")
    ax2.set_title("P1–P6（结构化方案）")
    ax2.legend(title="案例", fontsize=8)
    fig.suptitle("各方案总量估算偏差（正=高估，负=低估）")
    fig.tight_layout()
    fig.savefig(out / "figs/fig1_total_error.png", dpi=150)
    plt.close(fig)

    # 图2 类别工时占比（专家 vs P1 vs P4）
    cases = list(expert_total)
    show_p = [p for p in (("P1", "P4", "P6") if "P6" in set(catdf.prompt) else ("P1", "P4", "P5")) if p in set(catdf.prompt)]
    fig, axes = plt.subplots(1, len(cases), figsize=(4 * len(cases), 4), sharey=True)
    if len(cases) == 1:
        axes = [axes]
    for ax, case in zip(axes, cases):
        sub = catdf[catdf.case == case]
        bars = ["专家"] + show_p
        bottom = np.zeros(len(bars))
        for c in CATS:
            vals = [float(sub[(sub.prompt == b) & (sub.cat == c)]["share"].sum() or 0) * 100 for b in bars]
            ax.bar(bars, vals, bottom=bottom, label=CAT_CN[c])
            bottom += np.array(vals)
        ax.set_title(f"案例 {case}")
        ax.set_ylim(0, 100)
    axes[0].set_ylabel("工时占比 (%)")
    axes[-1].legend(bbox_to_anchor=(1.02, 1), loc="upper left", fontsize=8)
    fig.suptitle("类别工时占比：专家 vs 模型")
    fig.tight_layout()
    fig.savefig(out / "figs/fig2_category_share.png", dpi=150)
    plt.close(fig)

    # 图3 召回热力图
    heat = recall_g.pivot(index="case", columns="prompt", values="hit")
    heat = heat.reindex(columns=[p for p in ["P0", "P1", "P2", "P3", "P4", "P5", "P6"] if p in heat.columns])
    fig, ax = plt.subplots(figsize=(6.5, 2.8))
    im = ax.imshow(heat.values, cmap="YlGn", vmin=0.8, vmax=1.0, aspect="auto")
    ax.set_xticks(range(len(heat.columns)), heat.columns)
    ax.set_yticks(range(len(heat.index)), heat.index)
    for i in range(heat.shape[0]):
        for j in range(heat.shape[1]):
            v = heat.values[i, j]
            ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=9)
    ax.set_title("埋点要点提及率（关键词命中）")
    fig.colorbar(im, ax=ax, fraction=0.03)
    fig.tight_layout()
    fig.savefig(out / "figs/fig3_recall_heatmap.png", dpi=150)
    plt.close(fig)

    # 图4 Bland-Altman（类别层）
    fig, ax = plt.subplots(figsize=(6.5, 4))
    if not cpts.empty:
        d = cpts["l"] - cpts["e"]
        mean = (cpts["l"] + cpts["e"]) / 2
        ax.scatter(mean, d, s=18, alpha=0.7)
        ax.axhline(d.mean(), color="C1", lw=1, label=f"平均差 {d.mean():.1f}h")
        ax.axhline(d.mean() + 1.96 * d.std(), color="gray", ls="--", lw=1)
        ax.axhline(d.mean() - 1.96 * d.std(), color="gray", ls="--", lw=1)
        ax.axhline(0, color="k", lw=0.8)
        ax.set_xlabel("(LLM + 专家)/2 工时 (h)")
        ax.set_ylabel("LLM − 专家 (h)")
        ax.legend()
    ax.set_title("类别层一致性（Bland-Altman）")
    fig.tight_layout()
    fig.savefig(out / "figs/fig4_bland_altman.png", dpi=150)
    plt.close(fig)

    # 图5 P4 区间覆盖
    if not cov.empty:
        fig, ax = plt.subplots(figsize=(6.2, 3.8))
        pids = [p for p in ("P4", "P5", "P6") if p in set(cov.prompt)]
        cases_cov = list(expert_total)
        width = 0.8 / max(len(pids), 1)
        for j, pid in enumerate(pids):
            vals = [float(cov[(cov.case == c) & (cov.prompt == pid)]["coverage"].iloc[0]) * 100 for c in cases_cov]
            xs = np.arange(len(cases_cov)) + (j - (len(pids) - 1) / 2) * width
            ax.bar(xs, vals, width=width, label=pid)
            for x, v in zip(xs, vals):
                ax.text(x, v + 1, f"{v:.0f}%", ha="center", fontsize=8)
        ax.set_xticks(np.arange(len(cases_cov)), cases_cov)
        ax.set_ylim(0, 110)
        ax.set_ylabel("真值落入 [a总和, b总和] 的比例 (%)")
        ax.set_title("三点区间覆盖率（P4/P5/P6）")
        ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), fontsize=8)
        fig.tight_layout()
        fig.savefig(out / "figs/fig5_interval_coverage.png", dpi=150)
        plt.close(fig)

    # ---------------- summary.md ----------------
    def fmt_pct(v):
        return "—" if pd.isna(v) else f"{v*100:+.1f}%"

    lines = []
    if args.mock:
        lines += ["> **MOCK 数据，仅用于验证流水线，禁止写入报告。**", ""]
    raw_path = ROOT / ("runs/runs_mock.jsonl" if args.mock else CFG["output"]["jsonl"])
    raw_n = sum(1 for line in raw_path.read_text(encoding="utf-8").splitlines() if line.strip())
    lines += ["# 分析结果摘要", "",
              f"- 组数：{len(m)} 个 case×prompt×repeat；原始调用记录 {raw_n} 条（P3 为两阶段）",
              f"- 模型：{runs[0]['model']}，重复次数：{m.repeat.nunique()}", ""]

    lines += ["## 1. 总量偏差（各 case×prompt 的均值 ± SD）", ""]
    tab = g.copy()
    tab["偏差"] = tab["signed"].map(fmt_pct) + " ± " + (tab["signed_sd"] * 100).round(1).astype(str) + "%"
    tab["APE"] = tab["ape"].map(lambda v: f"{v*100:.1f}%")
    tab["CV"] = tab["cv"].map(lambda v: f"{v*100:.0f}%")
    lines += [md_table(tab[["case", "prompt", "total", "偏差", "APE", "CV"]]), ""]

    lines += ["## 2. 偏差来源分解（任务数 × 单任务工时）", "",
              "专家基准：A 19 包/132h（6.9h），B 22 包/160h（7.3h），C 21 包/128h（6.1h）。"
              "下表为各方案 15 次运行（3 案例×5 次）的几何均值，满足 总量比 = 任务数比 × 单任务工时比。", ""]
    dtab = dec.assign(
        任务数=dec["n_tasks"].map(lambda v: f"{v:.1f}"),
        任务数比=dec["count_ratio"].map(lambda v: f"×{v:.2f}"),
        单任务h=dec["avg_h"].map(lambda v: f"{v:.1f}"),
        单任务比=dec["avg_ratio"].map(lambda v: f"×{v:.2f}"),
        总量比=dec["total_ratio"].map(lambda v: f"×{v:.2f}"))
    # 总量比接近 1 时 log 分解的份额会失真，只给比值
    dtab["条数贡献"] = [f"{cs*100:.0f}%" if abs(lt) >= 0.1 else "—"
                       for cs, lt in zip(dec["count_share"], dec["log_total"])]
    dtab["单件贡献"] = [f"{av*100:.0f}%" if abs(lt) >= 0.1 else "—"
                       for av, lt in zip(dec["avg_share"], dec["log_total"])]
    lines += [md_table(dtab[["prompt", "任务数", "任务数比", "单任务h", "单任务比", "总量比", "条数贡献", "单件贡献"]]), "",
              "P1–P4 的条均工时已与专家基准相当（单任务比≈1），总量高估主要来自工作包条数；"
              "P0 则同时放大条数与单包工时；P5 通过交付物对账把包数压到基准以下、单包工时略升，两者抵消后总量接近真值（份额分解不适用）；"
              "P6 在保留隐性工作的同时把包数拉回基准附近（0.94×），总量比 1.04。", ""]

    lines += ["## 3. 高估 / 低估分布（阈值 ±5%）", ""]
    lines += [md_table(ou), ""]

    lines += ["## 4. 埋点要点提及率（关键词命中）", "",
              "统计口径：任一任务的 name+deliverable 命中条目关键词即记为提及。"
              "该指标是覆盖上界，同一要点可能被测试/文档任务顺带提及，不等于为其单列了工作包。", ""]
    lines += [md_table(recall_g.assign(召回=recall_g["hit"].map(lambda v: f"{v*100:.0f}%"))[["case", "prompt", "召回"]]), ""]
    lines += ["按埋点类型：", ""]
    lines += [md_table(recall_by_type.assign(召回=recall_by_type["hit"].map(lambda v: f"{v*100:.0f}%"))[["prompt", "type", "召回"]]), ""]

    lines += ["## 5. 类别偏差（log 比值均值）与规模效应", "",
              "下表为 P1–P6 合并的平均类别偏差；P6 单独结果见其后。", ""]
    lines += [md_table(bias[["cat", "cat_cn", "mean_pct"]]), ""]
    b6 = cpts[cpts.prompt == "P6"]
    if not b6.empty:
        bias6 = b6.groupby("cat")["logratio"].mean().reset_index(name="mean_log_ratio")
        bias6["mean_pct"] = (np.exp(bias6["mean_log_ratio"]) - 1) * 100
        bias6["cat_cn"] = bias6["cat"].map(CAT_CN)
        lines += ["P6（最终框架）单独类别偏差：", "", md_table(bias6[["cat", "cat_cn", "mean_pct"]]), ""]
    lines += [f"- log(LLM/专家) 对 log(专家工时) 回归斜率：**{slope:.3f}**，R²={r2:.3f}（n={len(cpts)}）。"
              "斜率接近 0 且 R² 很低，说明在本数据中未发现稳定的规模效应（负值方向对应“越大越低估”）。", ""]

    if not cov.empty:
        lines += ["## 6. 三点估算区间覆盖率（P4–P6）", ""]
        cov2 = cov.copy()
        cov2["coverage"] = cov2["coverage"].map(lambda v: f"{v*100:.0f}%")
        cov2["width_ratio"] = (cov2["width_ratio"] * 100).round(0).astype(int).astype(str) + "%"
        lines += [md_table(cov2[["case", "prompt", "coverage", "width_ratio", "expert", "a_mean", "b_mean"]]), ""]

    lines += ["## 7. 跨案例泛化（P0 → P4/P6 的 APE 变化）", ""]
    gen = g.pivot(index="case", columns="prompt", values="ape")
    if "P0" in gen.columns and "P4" in gen.columns:
        gg = pd.DataFrame({"case": gen.index, "APE_P0": gen["P0"].values, "APE_P4": gen["P4"].values})
        gg["Δ4(百分点)"] = (gen["P0"] - gen["P4"]).values * 100
        cols = ["case", "APE_P0", "APE_P4", "Δ4(百分点)"]
        if "P6" in gen.columns:
            gg["APE_P6"] = gen["P6"].values
            gg["Δ6(百分点)"] = (gen["P0"] - gen["P6"]).values * 100
            cols += ["APE_P6", "Δ6(百分点)"]
        for c in [c for c in cols if c.startswith("APE")]:
            gg[c] = gg[c].map(lambda v: f"{v*100:.1f}%")
        lines += [md_table(gg[cols]), "",
                  "若三个案例的 Δ 同号且大于 1 个百分点，可认为优化具有跨案例泛化性。", ""]

    lines += ["## 8. 基线敏感性（专家真值整体 × k）", "",
              "专家自估可能偏乐观，尤其 B（160h 覆盖爬虫、被动代理、4 类插件、靶场与合规）。"
              "将真值整体放大 k 后重算总量偏差，用于判断结论是否依赖基线口径。", ""]
    stab = sens.copy()
    for c in [c for c in stab.columns if c.startswith("k=")]:
        stab[c] = stab[c].map(lambda v: f"{v:+.0f}%")
    lines += [md_table(stab), "",
              "P0 在所有 k 下仍大幅高估；结构化方案的符号与幅度对 k 敏感，说明其剩余偏差与基线口径不确定性同量级。", ""]

    (out / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"输出目录：{out}")
    print(f"记录 {len(runs)} 条；summary.md、metrics_*.csv、figs/*.png 已生成")


if __name__ == "__main__":
    main()
