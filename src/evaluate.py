"""Phase 5: score Clef's recorded answers against the labels.

  python src/evaluate.py runs/<run_id>      # default: the newest run

Writes runs/<run_id>/metrics.json, reports/accuracy.md and two PNG charts in reports/.
Free to re-run: it reads only files.

Labels come from data/labels/labels.csv (src/make_labels.py); a blank cell is not scored, which
is how the scorebug questions are skipped on frames where the scorebug is hidden. Questions
listed under `soft_labels` in the schema are marked with * and kept out of the headline figure.
"""
import argparse
import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from clef_client import load_schema  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GROUPS = {
    "A. Layout": ["main_view", "scorebug_visible", "replay_showing", "info_overlay"],
    "B. Scorebug": ["quarter", "clock_minutes", "jets_score", "bengals_score", "scorebug_message", "panel_colour",
                    "possession", "down", "distance", "jets_timeouts", "bengals_timeouts", "review_banner"],
    "C. Scene": ["play_phase", "yard_markers_visible", "yards_to_goal", "celebration"],
    "D. Editorial": ["highlight_worthy", "ad_break_safe", "sponsor_visible", "sponsor_placement"],
}
# options are in order, so "one step away" means something (escape options are left out of the order)
ORDERED = ["clock_minutes", "jets_score", "bengals_score", "distance", "jets_timeouts", "bengals_timeouts", "yards_to_goal"]
ESCAPES = {"not_visible", "not_shown", "cant_tell", "none", "goal"}
CONFUSIONS = ["main_view", "play_phase", "possession"]
CONF_BINS = [(0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0001)]


def macro_f1(truth, pred):
    f1s = []
    for c in set(truth):
        tp = sum(1 for t, p in zip(truth, pred) if t == c and p == c)
        fp = sum(1 for t, p in zip(truth, pred) if t != c and p == c)
        fn = sum(1 for t, p in zip(truth, pred) if t == c and p != c)
        f1s.append(2 * tp / (2 * tp + fp + fn) if tp else 0.0)
    return sum(f1s) / len(f1s)


def load_labels():
    with (ROOT / "data/labels/labels.csv").open(encoding="utf-8", newline="") as fh:
        return {r["frame"]: r for r in csv.DictReader(fh)}


def load_pairs(run_dir, schema, labels):
    """Return {qid: [(frame, label, answer, confidence), ...]}; blank and '?' labels are dropped."""
    answers = [json.loads(line) for line in (run_dir / "answers.jsonl").read_text(encoding="utf-8").splitlines() if line]
    pairs = defaultdict(list)
    for row in answers:
        lab = labels.get(row["frame"])
        if lab is None:
            continue
        for q in schema["questions"]:
            a = row["answers"].get(q["id"])
            truth = (lab.get(q["id"]) or "").strip()
            if a is None or truth in ("", "?"):
                continue
            pairs[q["id"]].append((row["frame"], truth, a["answer"], a["confidence"]))
    return pairs


def question_metrics(qid, rows, options):
    truth = [r[1] for r in rows]
    pred = [r[2] for r in rows]
    n = len(rows)
    acc = sum(t == p for t, p in zip(truth, pred)) / n
    baseline = Counter(truth).most_common(1)[0][1] / n
    m = {"n": n, "accuracy": acc, "majority_baseline": baseline, "macro_f1": macro_f1(truth, pred),
         "mean_confidence": sum(r[3] for r in rows) / n}
    if qid in ORDERED:
        order = [o for o in options if o not in ESCAPES]
        near = sum(1 for t, p in zip(truth, pred)
                   if t in order and p in order and abs(order.index(t) - order.index(p)) == 1)
        m["near_miss_rate"] = near / n
    return m


def confusion(rows, options):
    cm = {t: Counter() for t in options}
    for _, t, p, _ in rows:
        cm[t][p] += 1
    used = [o for o in options if any(cm[o].values()) or any(cm[t][o] for t in options)]
    return used, cm


def md_table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def pct(x):
    return f"{100 * x:.0f}%"


def pooled(cells):
    return sum(r[1] == r[2] for r in cells) / len(cells) if cells else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", nargs="?")
    args = ap.parse_args()
    runs = sorted(p for p in (ROOT / "runs").glob("run_*") if (p / "answers.jsonl").exists())
    run_dir = Path(args.run_dir) if args.run_dir else (runs[-1] if runs else None)
    if run_dir is None:
        raise SystemExit("no run with answers.jsonl found under runs/")
    schema = load_schema()
    soft = set(schema.get("soft_labels", []))
    options = {q["id"]: q["options"] for q in schema["questions"]}
    labels = load_labels()
    pairs = load_pairs(run_dir, schema, labels)
    meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
    star = lambda q: q + (" *" if q in soft else "")  # noqa: E731

    per_q = {qid: question_metrics(qid, rows, options[qid]) for qid, rows in pairs.items()}
    group_acc, group_firm = {}, {}
    for g, ids in GROUPS.items():
        group_acc[g] = pooled([r for qid in ids for r in pairs.get(qid, [])])
        group_firm[g] = pooled([r for qid in ids if qid not in soft for r in pairs.get(qid, [])])
    cells = [r for rows in pairs.values() for r in rows]
    firm_cells = [r for qid, rows in pairs.items() if qid not in soft for r in rows]

    # scores: exact match and within one point
    score_rows = []
    for qid in ("jets_score", "bengals_score"):
        num = [(t, p) for _, t, p, _ in pairs.get(qid, []) if t.isdigit()]
        exact = sum(t == p for t, p in num)
        close = sum(p.isdigit() and abs(int(t) - int(p)) <= 1 for t, p in num)
        score_rows.append([qid, len(num), f"{exact} ({pct(exact / len(num))})", f"{close} ({pct(close / len(num))})",
                           sum(not p.isdigit() for _, p in num)])

    # celebration by team: how often each true label is found
    celeb = Counter((t, p) for _, t, p, _ in pairs.get("celebration", []))
    celeb_rows = [[t, sum(v for (tt, _), v in celeb.items() if tt == t), celeb[(t, t)],
                   ", ".join(f"{p} {v}" for (tt, p), v in sorted(celeb.items()) if tt == t and p != t) or "none"]
                  for t in options.get("celebration", [])]

    # does it read the scorebug as well on every kind of shot?
    by_view = defaultdict(list)
    replay = defaultdict(list)
    for qid in GROUPS["B. Scorebug"]:
        for r in pairs.get(qid, []):
            by_view[labels[r[0]]["main_view"]].append(r)
            replay[labels[r[0]]["replay_showing"]].append(r)
    view_rows = [[v, len({r[0] for r in rs}), len(rs), pct(pooled(rs))] for v, rs in sorted(by_view.items(), key=lambda kv: -len(kv[1]))]
    replay_rows = [[v, len({r[0] for r in rs}), len(rs), pct(pooled(rs))] for v, rs in sorted(replay.items())]

    calib = []
    for lo, hi in CONF_BINS:
        b = [r for r in cells if lo <= r[3] < hi]
        calib.append({"bin": f"{lo:.1f}-{min(hi, 1.0):.1f}", "n": len(b),
                      "mean_confidence": sum(r[3] for r in b) / len(b) if b else None,
                      "accuracy": pooled(b)})

    wrong, wrong_qs = Counter(), defaultdict(list)
    for qid, rows in pairs.items():
        for frame, t, p, _ in rows:
            if t != p:
                wrong[frame] += 1
                wrong_qs[frame].append(f"{star(qid)} (label {t}, Clef {p})")
    worst = wrong.most_common(10)

    raws = [json.loads(f.read_text(encoding="utf-8")) for f in (run_dir / "raw").glob("*.json")]
    latencies = [r["_meta"]["latency_s"] for r in raws if r.get("_meta", {}).get("latency_s")]
    total_cost = sum(r.get("usage", {}).get("cost", 0) or 0 for r in raws)
    total_tokens = sum(r.get("usage", {}).get("input_tokens", 0) or 0 for r in raws)
    per_hour = total_cost / len(raws) * 3600 / 5 if raws else 0  # one frame every 5 s

    metrics = {"run_id": meta["run_id"], "schema_version": meta["schema_version"], "per_question": per_q,
               "soft_labels": sorted(soft), "group_accuracy": group_acc, "group_accuracy_firm": group_firm,
               "headline_accuracy_firm": pooled(firm_cells), "calibration": calib, "worst_frames": worst,
               "cost_usd_all_frames": total_cost, "input_tokens_all_frames": total_tokens,
               "cost_usd_per_broadcast_hour": per_hour,
               "latency_median_s": statistics.median(latencies) if latencies else None,
               "latency_p95_s": sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)] if latencies else None}
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    qids = [q for ids in GROUPS.values() for q in ids if q in per_q]

    # charts
    fig, ax = plt.subplots(figsize=(12, 5))
    xs = range(len(qids))
    ax.bar([x - 0.2 for x in xs], [per_q[q]["accuracy"] for q in qids], 0.4, label="Clef accuracy")
    ax.bar([x + 0.2 for x in xs], [per_q[q]["majority_baseline"] for q in qids], 0.4, label="majority baseline", color="#bbb")
    ax.set_xticks(list(xs))
    ax.set_xticklabels([star(q) for q in qids], rotation=70, ha="right")
    ax.set_ylim(0, 1.05)
    ax.legend(loc="lower right")
    ax.set_title("Accuracy vs majority baseline (* = soft labels)")
    fig.tight_layout()
    fig.savefig(reports / "accuracy_vs_baseline.png", dpi=110)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(5, 5))
    pts = [(c["mean_confidence"], c["accuracy"]) for c in calib if c["n"]]
    ax.plot([0, 1], [0, 1], "--", color="#999")
    ax.plot([p[0] for p in pts], [p[1] for p in pts], "o-")
    ax.set_xlabel("mean confidence")
    ax.set_ylabel("accuracy")
    ax.set_title("Calibration")
    fig.tight_layout()
    fig.savefig(reports / "calibration.png", dpi=110)
    plt.close(fig)

    # report
    hidden = sum(1 for r in labels.values() if r.get("scorebug_visible") == "no")
    L = [f"# Accuracy report: {meta['run_id']}", "",
         f"Model `{meta['model']}`, schema v{meta['schema_version']}, {meta['frames']} frames (the 4th quarter, one every 5 s), "
         f"{len(cells)} scored answers. Scorebug crop sent as a second image: {'yes' if meta.get('scorebug_crop') else 'no'}.", "",
         "**About the labels.**", "",
         "- **Group B (scorebug):** read from every frame by Claude and checked against the official nflverse "
         "play-by-play; the two agree on all but 12 frames, where the label follows what the broadcast shows. "
         f"The scorebug is hidden on {hidden} frames; group B is not scored there.",
         "- **Groups A, C, D:** drafted by Claude from the frames; the maintainer spot-checked a sample and corrected "
         "what was found. They are **AI-drafted and only partly human-reviewed.**",
         "- **Questions marked \\*** are soft labels: one reader's judgement from a single still, known to contain "
         "errors (sponsor labels especially). Their numbers are indicative only and are left out of the headline.", "",
         "## Headline", "",
         f"**{pct(pooled(firm_cells))}** of answers correct on the {len([q for q in qids if q not in soft])} firm-label "
         f"questions ({len(firm_cells)} answers); **{pct(group_firm['B. Scorebug'])}** on reading the scorebug.", "",
         "## Summary", "",
         md_table(["Question", "n", "Accuracy", "Majority baseline", "Macro-F1", "Near-miss"],
                  [[star(q), per_q[q]["n"], pct(per_q[q]["accuracy"]), pct(per_q[q]["majority_baseline"]),
                    f"{per_q[q]['macro_f1']:.2f}", pct(per_q[q]["near_miss_rate"]) if "near_miss_rate" in per_q[q] else ""]
                   for q in qids]),
         "", "The majority baseline is the accuracy of always giving that question's most common label. A question "
         "only \"works\" if Clef clearly beats it. Near-miss is the share of answers exactly one option away.", "",
         "## By group", "",
         md_table(["Group", "Accuracy, all questions", "Accuracy, firm labels only"],
                  [[g, pct(group_acc[g]) if group_acc[g] is not None else "n/a",
                    pct(group_firm[g]) if group_firm[g] is not None else "no firm questions"] for g in GROUPS]),
         "", "![accuracy vs baseline](accuracy_vs_baseline.png)", "",
         "## Scores", "",
         md_table(["Question", "Frames with a score", "Exact", "Within 1 point", "Answered not_visible"], score_rows), "",
         "## Timeouts and celebration by team", "",
         md_table(["Question", "n", "Accuracy", "Majority baseline"],
                  [[q, per_q[q]["n"], pct(per_q[q]["accuracy"]), pct(per_q[q]["majority_baseline"])]
                   for q in ("jets_timeouts", "bengals_timeouts") if q in per_q]), "",
         "Celebration \\*, by true label:", "",
         md_table(["True label", "Frames", "Found", "Answered instead"], celeb_rows), "",
         "## Scorebug reading by kind of shot", "",
         "Pooled accuracy on the group B questions, split by the labelled `main_view` of the frame.", "",
         md_table(["main_view", "Frames", "Answers", "Scorebug accuracy"], view_rows), "",
         "Split by `replay_showing` \\* (almost every replay hides the scorebug, so the `yes` row is small):", "",
         md_table(["replay_showing", "Frames", "Answers", "Scorebug accuracy"], replay_rows), ""]
    for qid in CONFUSIONS:
        if qid in pairs:
            used, cm = confusion(pairs[qid], options[qid])
            L += [f"## Confusion matrix: {star(qid)}", "", "Rows = label, columns = Clef.", "",
                  md_table(["label \\ Clef"] + used, [[t] + [cm[t][p] or "" for p in used] for t in used]), ""]
    L += ["## Calibration", "", "All questions pooled.", "",
          md_table(["Confidence bin", "n", "Mean confidence", "Accuracy"],
                   [[c["bin"], c["n"], f"{c['mean_confidence']:.2f}" if c["n"] else "", pct(c["accuracy"]) if c["n"] else ""]
                    for c in calib]),
          "", "![calibration](calibration.png)", "", "## 10 worst frames", "",
          md_table(["Frame", "Wrong answers", "Which"],
                   [[f"data/frames/{f}", n, "; ".join(wrong_qs[f][:5]) + (" ..." if len(wrong_qs[f]) > 5 else "")]
                    for f, n in worst]),
          "", "## Cost and latency", "",
          f"- Input tokens, all frames: {total_tokens:,}",
          f"- Cost, all frames: ${total_cost:.4f} (${total_cost / len(raws):.5f} per frame; "
          f"${per_hour:.2f} per broadcast hour at one frame every 5 s)",
          f"- Latency: median {metrics['latency_median_s']} s, p95 {metrics['latency_p95_s']} s", "",
          "Play-by-play: nflverse (https://github.com/nflverse).", ""]
    (reports / "accuracy.md").write_text("\n".join(L), encoding="utf-8")
    print(f"wrote reports/accuracy.md, charts and {run_dir.name}/metrics.json")
    print("\n".join(L[L.index("## Headline"):L.index("## Calibration")]))
    print("\n".join(L[L.index("## Calibration"):]))


if __name__ == "__main__":
    main()
