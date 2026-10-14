"""Phase 6: turn Clef's per-frame answers into football events and score them. No API calls.

  python src/detect_events.py [runs/<run_id>]     # default: the latest run

Smoothing is the same as in clef-video-probe. An answer is trusted if its confidence is at least
`min_conf` and it is not an escape option. A state changes only when a new trusted value holds for
`persist_frames` frames in a row; untrusted and escape answers are skipped, they neither start nor
break a streak. The event time is the first frame of the new state.

Rules on the smoothed states (README, Logic 4, adapted to schema v2):
  touchdown_<team> / field_goal_<team>   that team's score rises by 6 / 3 (7 or 8 = touchdown + conversion)
  extra_point_good / two_point_good      the scoring team's score rises by 1 / 2 after its touchdown
  conversion_no_good                     the other team gets the ball after a touchdown with no further score
  safety_<team>                          a score rises by 2 with no touchdown pending
  tie / lead_change                      the scores become level / a different team leads
  possession_change                      possession flips with no score since the old team last had the ball;
                                         subtype punt (a punt was seen), turnover_on_downs (last down was 4), else turnover
  timeout_<team>                         that team's timeouts drop by 1
  penalty / replay_review                the scorebug message becomes FLAG / UNDER REVIEW
  game_end                               play_phase = kneel_down while the clock shows 0 minutes
  first_down, two_point_attempt          reported, not scored (not in the truth list)

Departure from the README: it ties a conversion to "within 4 frames after a TD". In this broadcast the
conversion lands 7 to 38 frames after the touchdown (replays, a review), so the rule here is "the next
1- or 2-point rise by the team that just scored a touchdown".

A detection is a hit if it is the same event within `events.hit_window_s` (default -5 s to +20 s) of
a true one in data/labels/events_truth.csv. Writes runs/<run_id>/events.json and the "Event
detection" section of reports/accuracy.md (run evaluate.py first; it rewrites the report).
"""
import argparse
import csv
import json
from pathlib import Path

from clef_client import load_config, load_schema
from evaluate import md_table

ROOT = Path(__file__).resolve().parent.parent
NO_INFO = {"cant_tell", "not_visible", "not_shown", "none"}
TEAMS = ("jets", "bengals")
UNSCORED = {"first_down", "two_point_attempt"}
WATCHED = ["jets_score", "bengals_score", "possession", "down", "jets_timeouts", "bengals_timeouts", "scorebug_message"]
SECTION = "## Event detection"


def trusted(row, qid, min_conf):
    a = row["answers"].get(qid)
    return a["answer"] if a and a["confidence"] >= min_conf and a["answer"] not in NO_INFO else None


def stable_changes(rows, qid, min_conf, persist):
    """Yield (old, new, first_row, confidences, last_row) each time the smoothed state of `qid` changes.
    last_row is the frame that completed the streak: the earliest a live system could report it."""
    state, cand, streak = None, None, []
    for row in rows:
        a = row["answers"].get(qid)
        if a is None or a["confidence"] < min_conf or a["answer"] in NO_INFO:
            continue
        if a["answer"] == state:
            cand, streak = None, []
            continue
        if a["answer"] != cand:
            cand, streak = a["answer"], []
        streak.append((row, a["confidence"]))
        if len(streak) >= persist:
            yield state, cand, streak[0][0], [round(c, 3) for _, c in streak], streak[-1][0]
            state, cand, streak = cand, None, []


def detect(rows, min_conf, persist):
    for i, row in enumerate(rows):
        row["i"] = i
    changes = [(row["i"], WATCHED.index(qid), qid, old, new, row, confs, done)
               for qid in WATCHED for old, new, row, confs, done in stable_changes(rows, qid, min_conf, persist)]
    changes.sort(key=lambda c: c[:2])
    events = []

    def add(name, qid, old, new, row, confs, done, subtype=""):
        events.append({"event": name, "subtype": subtype, "t_seconds": row["t_seconds"], "frame": row["frame"],
                       "confirmed_t_seconds": done["t_seconds"], "confirmed_frame": done["frame"],
                       "evidence": {"question": qid, "from": old, "to": new, "confidences": confs}})

    score = {t: None for t in TEAMS}
    leader = None          # +1 jets, -1 bengals; the last team to hold a lead
    pending_td = None      # team that scored a touchdown and has not converted yet
    last_score_i = -1
    down = None
    for i, _, qid, old, new, row, confs, done in changes:
        args = (qid, old, new, row, confs, done)
        if qid in ("jets_score", "bengals_score"):
            team = qid.split("_")[0]
            before, score[team] = score[team], int(new)
            if before is not None:
                rise = score[team] - before
                last_score_i = i
                if rise in (6, 7, 8):
                    add(f"touchdown_{team}", *args)
                    pending_td = team
                    if rise > 6:
                        add("extra_point_good" if rise == 7 else "two_point_good", *args)
                        pending_td = None
                elif rise == 3:
                    add(f"field_goal_{team}", *args)
                elif rise in (1, 2) and pending_td == team:
                    add("extra_point_good" if rise == 1 else "two_point_good", *args)
                    pending_td = None
                elif rise == 2:
                    add(f"safety_{team}", *args)
            if None not in score.values():
                diff = score["jets"] - score["bengals"]
                sign = (diff > 0) - (diff < 0)
                if before is not None and sign == 0:
                    add("tie", *args)
                elif sign and leader is not None and sign != leader:
                    add("lead_change", *args)
                leader = sign or leader
        elif qid == "possession":
            if old is not None:
                held = [r["i"] for r in rows[:i] if trusted(r, "possession", min_conf) == old]
                since = held[-1] if held else -1
                if last_score_i > since:       # a kickoff after a score, not a turnover
                    if pending_td == old:
                        add("conversion_no_good", *args)
                    pending_td = None
                else:
                    punted = any(trusted(r, "play_phase", min_conf) == "punt" for r in rows[since:i + 1])
                    add("possession_change", *args, subtype="punt" if punted else "turnover_on_downs" if down == "4" else "turnover")
            down = None
        elif qid == "down":
            if new == "1" and old in ("2", "3", "4") and down is not None:
                add("first_down", *args)
            down = new
        elif qid.endswith("_timeouts"):
            if old is not None and int(new) == int(old) - 1:
                add(f"timeout_{qid.split('_')[0]}", *args)
        elif qid == "scorebug_message":
            name = {"flag": "penalty", "under_review": "replay_review", "two_point_attempt": "two_point_attempt"}.get(new)
            if name:
                add(name, *args)
    for old, new, row, confs, done in stable_changes(rows, "play_phase", min_conf, persist):
        if new == "kneel_down" and trusted(row, "clock_minutes", min_conf) == "0":
            add("game_end", "play_phase", old, new, row, confs, done)
            break
    return sorted(events, key=lambda e: e["t_seconds"])


def score_events(events, truth, window):
    """Match each true event to the nearest unused detection of the same name inside the window."""
    used, out = set(), []
    for t in truth:
        best = None
        for k, e in enumerate(events):
            if k in used or e["event"] != t["event"]:
                continue
            delay = e["t_seconds"] - t["t"]
            if window[0] <= delay <= window[1] and (best is None or abs(delay) < abs(best[1])):
                best = (k, delay)
        if best:
            used.add(best[0])
        out.append({**t, "detected_t": events[best[0]]["t_seconds"] if best else None,
                    "delay_s": round(best[1], 1) if best else None,
                    "detected_subtype": events[best[0]]["subtype"] if best else None})
    false_pos = [e for k, e in enumerate(events) if k not in used and e["event"] not in UNSCORED]
    return out, false_pos


def totals(scored, false_pos):
    hits = sum(s["detected_t"] is not None for s in scored)
    return hits, len(scored) - hits, len(false_pos)


def clock(t):
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", nargs="?")
    args = ap.parse_args()
    runs = sorted(p for p in (ROOT / "runs").glob("run_*") if (p / "answers.jsonl").exists())
    run_dir = Path(args.run_dir) if args.run_dir else (runs[-1] if runs else None)
    if run_dir is None:
        raise SystemExit("no run with answers.jsonl found under runs/")
    cfg = load_config()
    load_schema()
    min_conf, persist = cfg["events"]["min_conf"], cfg["events"]["persist_frames"]
    window = cfg["events"]["hit_window_s"]
    rows = [json.loads(line) for line in (run_dir / "answers.jsonl").read_text(encoding="utf-8").splitlines() if line]
    rows.sort(key=lambda r: r["t_seconds"])
    with (ROOT / "data/labels/events_truth.csv").open(encoding="utf-8", newline="") as fh:
        truth = [{"event": r["event"], "subtype": r["subtype"], "t": float(r["t_video"]), "game_clock": r["game_clock"],
                  "frame": r["frame"]} for r in csv.DictReader(fh) if r["t_video"].strip()]

    events = detect(rows, min_conf, persist)
    scored, false_pos = score_events(events, truth, window)
    hits, misses, fps = totals(scored, false_pos)
    grid = {(c, p): totals(*score_events(detect(rows, c, p), truth, window)) for c in (0.5, 0.6, 0.7) for p in (1, 2, 3)}
    best = min(grid, key=lambda k: (-grid[k][0], grid[k][2], k != (min_conf, persist)))
    delays = [s["delay_s"] for s in scored if s["delay_s"] is not None]

    (run_dir / "events.json").write_text(json.dumps({
        "run_id": run_dir.name, "min_conf": min_conf, "persist_frames": persist, "hit_window_s": window,
        "events": events, "scoring": scored, "false_positives": false_pos,
        "sensitivity": [{"min_conf": c, "persist_frames": p, "hits": h, "misses": m, "false_positives": f}
                        for (c, p), (h, m, f) in grid.items()],
    }, indent=2), encoding="utf-8")

    def name(e):
        return e["event"] + (f" ({e['subtype']})" if e["subtype"] else "")

    unscored = [e for e in events if e["event"] in UNSCORED]
    L = [SECTION, "",
         f"Settings: `min_conf` {min_conf}, `persist_frames` {persist}, hit window {window[0]:+g} s to {window[1]:+g} s. "
         f"**{hits} of {len(truth)} true events hit, {misses} missed, {fps} false positives.**"
         + (f" Median delay on hits {sorted(delays)[len(delays) // 2]:+g} s." if delays else ""), "",
         "True events are timed at the first frame where the scorebug shows them.", "",
         md_table(["True event", "Game clock", "True time", "Detected", "Outcome", "Delay (s)"],
                  [[name(s), s["game_clock"], f"{s['t']:.0f} s ({clock(s['t'])})",
                    f"{s['detected_t']:.0f} s" if s["detected_t"] is not None else "none",
                    "miss" if s["detected_t"] is None else "hit" if not s["subtype"] or s["subtype"] == s["detected_subtype"]
                    or s["event"] == "replay_review" else f"hit, called {s['detected_subtype']}",
                    f"{s['delay_s']:+g}" if s["delay_s"] is not None else ""] for s in scored]), "",
         "### False positives", "",
         md_table(["Detected event", "Time", "Frame", "Evidence"],
                  [[name(e), f"{e['t_seconds']:.0f} s", e["frame"],
                    f"{e['evidence']['question']}: {e['evidence']['from']} -> {e['evidence']['to']} at {e['evidence']['confidences']}"]
                   for e in false_pos]) if false_pos else "None.", "",
         f"Not scored: `first_down` fired {sum(e['event'] == 'first_down' for e in unscored)} times and "
         f"`two_point_attempt` at {', '.join(f'{e['t_seconds']:.0f} s' for e in unscored if e['event'] == 'two_point_attempt') or 'no time'}.", "",
         "### Sensitivity", "", "Each cell is hits / misses / false positives.", "",
         md_table(["min_conf \\ persist_frames", "1", "2", "3"],
                  [[c] + ["{} / {} / {}".format(*grid[(c, p)]) for p in (1, 2, 3)] for c in (0.5, 0.6, 0.7)]),
         "", f"Most hits with fewest false positives: `min_conf` {best[0]}, `persist_frames` {best[1]} "
         f"({grid[best][0]} hits, {grid[best][2]} false positives).", ""]
    report = ROOT / "reports" / "accuracy.md"
    text = report.read_text(encoding="utf-8") if report.exists() else ""
    report.write_text(text.split(SECTION)[0].rstrip("\n") + "\n\n" + "\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"wrote {run_dir.name}/events.json and the Event detection section of reports/accuracy.md")


if __name__ == "__main__":
    main()
