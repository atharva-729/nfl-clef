"""Phase 3: assemble the label files the later phases read. No API calls. Run align_pbp.py first.

  python src/make_labels.py             # labels.csv only
  python src/make_labels.py --events    # also redraft events_truth.csv (overwrites hand-set times)

Writes  data/labels/labels.csv         one row per frame, one column per question: group B from
                                       labels_B.csv, groups A/C/D from labels_ACD_reviewed.csv if the
                                       maintainer has saved it, else from labels_ACD_draft.csv
                                       (column `acd_status` says which: `reviewed` or `draft`)
        data/labels/events_truth.csv   draft event list from the pbp, for the maintainer to confirm

An event is timed at the first frame, with the scorebug visible, whose aligned state comes after
the play: that is the earliest anyone watching the scorebug could know. `first_down` is not in the
truth list (the detector reports it, it is not scored).
"""
import csv
import sys
from pathlib import Path

import pandas as pd
import yaml

from align_pbp import B_QUESTIONS, LABELS, ROOT, TEAMS, load_states
from clef_client import load_schema

EVENT_FIELDS = ["event", "subtype", "t_video", "frame", "game_clock", "play_id", "description"]


def read_csv(path):
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def sign(x):
    return (x > 0) - (x < 0)


def play_events(r, before, after):
    """(event, subtype) pairs for one pbp row. `before`/`after` are {team: score} around the play."""
    def flag(col):
        return col in r and pd.notna(r[col]) and r[col] == 1

    def team(col):
        return TEAMS.get(r.get(col), "unknown")

    out = []
    if flag("touchdown"):
        out.append((f"touchdown_{team('td_team')}", ""))
    if r.get("field_goal_result") == "made":
        out.append((f"field_goal_{team('posteam')}", ""))
    if r.get("extra_point_result") == "good":
        out.append(("extra_point_good", ""))
    elif pd.notna(r.get("extra_point_result")):
        out.append(("conversion_no_good", "extra_point"))
    if r.get("two_point_conv_result") == "success":
        out.append(("two_point_good", ""))
    elif pd.notna(r.get("two_point_conv_result")):
        out.append(("conversion_no_good", "two_point"))
    if flag("safety"):
        out.append((f"safety_{team('defteam')}", ""))
    if flag("timeout"):
        out.append((f"timeout_{team('timeout_team')}", ""))
    if flag("penalty"):
        out.append(("penalty", ""))
    if flag("replay_or_challenge"):
        out.append(("replay_review", str(r.get("replay_or_challenge_result") or "")))
    if flag("punt_attempt"):
        out.append(("possession_change", "punt"))
    elif flag("fourth_down_failed"):
        out.append(("possession_change", "turnover_on_downs"))
    elif flag("interception") or flag("fumble_lost"):
        out.append(("possession_change", "turnover"))
    was, now = before["jets"] - before["bengals"], after["jets"] - after["bengals"]
    if now == 0 and was != 0:
        out.append(("tie", ""))
    elif now != 0 and sign(now) != sign(was):
        out.append(("lead_change", ""))
    return out


def main():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    schema = load_schema()
    qids = [q["id"] for q in schema["questions"]]
    b_rows = read_csv(LABELS / "labels_B.csv")

    reviewed = LABELS / "labels_ACD_reviewed.csv"
    acd_path, status = (reviewed, "reviewed") if reviewed.exists() else (LABELS / "labels_ACD_draft.csv", "draft")
    acd = {r["frame"]: r for r in read_csv(acd_path)} if acd_path.exists() else {}
    rows = []
    for b in b_rows:
        a = acd.get(b["frame"], {})
        row = {"frame": b["frame"], "t_actual": b["t_actual"], "acd_status": status if a else ""}
        for q in qids:
            row[q] = b[q] if q in B_QUESTIONS else (a.get(q) or "")
        rows.append(row)
    with (LABELS / "labels.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["frame", "t_actual", "acd_status", *qids])
        w.writeheader()
        w.writerows(rows)
    print(f"wrote data/labels/labels.csv ({len(rows)} frames; groups A/C/D: "
          + (f"{status}, from {acd_path.name}" if acd else "none yet") + ")")

    if "--events" not in sys.argv:
        return
    states = load_states(cfg)
    df = pd.read_csv(ROOT / cfg["game"]["pbp_path"], low_memory=False)
    plays = df[df["qtr"] == cfg["game"]["quarter"]].to_dict("records")
    visible = [(int(b["state_idx"]), b) for b in b_rows if b["scorebug_visible"] == "yes"]
    events = []
    for j, (r, s) in enumerate(zip(plays, states)):
        before = {"jets": s["jets_score"], "bengals": s["bengals_score"]}
        first = next((b for idx, b in visible if idx > j), None)
        for event, subtype in play_events(r, before, s["score_after"]):
            events.append({"event": event, "subtype": subtype, "t_video": first["t_actual"] if first else "",
                           "frame": first["frame"] if first else "", "game_clock": s["clock"],
                           "play_id": s["play_id"], "description": s["desc"][:160]})
    last = next((s for s in reversed(states) if "kneel" in s["desc"].lower()), None)
    if last:
        j = states.index(last)
        first = next((b for idx, b in visible if idx >= j), None)
        events.append({"event": "game_end", "subtype": "", "t_video": first["t_actual"] if first else "",
                       "frame": first["frame"] if first else "", "game_clock": last["clock"],
                       "play_id": last["play_id"], "description": last["desc"][:160]})
    with (LABELS / "events_truth.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=EVENT_FIELDS)
        w.writeheader()
        w.writerows(events)
    untimed = sum(1 for e in events if not e["t_video"])
    print(f"wrote data/labels/events_truth.csv: {len(events)} draft events"
          + (f", {untimed} with no frame after the play" if untimed else "") + " (maintainer to confirm)")
    for e in events:
        print(f"  {e['game_clock']:>5}  {e['event']}{' / ' + e['subtype'] if e['subtype'] else ''}  -> {e['t_video'] or '?'} s")


if __name__ == "__main__":
    main()
