"""Phase 3: ground truth for group B (the scorebug) from the draft reads and the play-by-play. No API calls.

  python src/align_pbp.py              # needs data/labels/scorebug_reads_draft.csv
  python src/align_pbp.py --plays      # just print the quarter's plays (Phase 0 check)

Every pbp row of the quarter is a pre-snap state (clock, possession, down, distance, ball spot,
scores, timeouts). Frames are in time order and states in game order, so each frame is assigned
to a state with a monotone alignment: a frame never goes back to an earlier state. The match is
on scores, possession, down and distance; the clock only breaks ties.

Reads   data/labels/scorebug_reads_draft.csv   one row per frame, blank = unreadable:
          frame, scorebug_visible, quarter, clock (M:SS), jets_score, bengals_score,
          message (3&2, 1&G, 2D = down only, FLAG, 2PT, REV, CBS, OTH), panel_colour, banner,
          jets_timeouts, bengals_timeouts
Writes  data/labels/labels_B.csv        truth per frame as schema options; blank where the scorebug is hidden
        data/labels/disagreements.csv   where the draft read and the aligned pbp state differ

The group B questions ask what the scorebug SHOWS, so the label is the read: the scorebug can
lawfully differ from the pbp state (a score appears a few seconds late, "2ND DOWN" is shown before
the distance, the down stays up during the play). The pbp is the check on the read: every
difference is listed in disagreements.csv for the maintainer. `pbp_yardline_100` is carried along
as the truth for `yards_to_goal`.
"""
import argparse
import csv
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
LABELS = ROOT / "data" / "labels"
TEAMS = {"NYJ": "jets", "CIN": "bengals"}
B_QUESTIONS = ["quarter", "clock_minutes", "jets_score", "bengals_score", "scorebug_message", "panel_colour",
               "possession", "down", "distance", "jets_timeouts", "bengals_timeouts", "review_banner"]
MESSAGES = {"FLAG": "flag", "2PT": "two_point_attempt", "REV": "under_review", "CBS": "network_logo", "OTH": "other"}
REVIEW_BANNERS = {"review_reason", "call_overturned", "call_stands"}
PBP_COLUMNS = ["play_id", "qtr", "time", "posteam", "down", "ydstogo", "yardline_100", "goal_to_go",
               "total_home_score", "total_away_score", "home_timeouts_remaining", "away_timeouts_remaining", "desc"]
WEIGHTS = {"jets_score": 3, "bengals_score": 3, "possession": 2, "down": 2, "distance": 1}


def clock_s(text):
    """'14:21' -> 861. Blank or unreadable -> None."""
    try:
        m, s = str(text).strip().split(":")[:2]
        return int(m) * 60 + int(s)
    except ValueError:
        return None


def parse_read(r):
    """A draft read row -> the scorebug as schema answers plus raw down / yards for the alignment."""
    msg, colour = r.get("message", ""), r.get("panel_colour", "")
    out = {**r, "down": "", "yards": "", "goal": False}
    if "&" in msg:
        out["down"], _, y = msg.partition("&")
        out["goal"], out["yards"] = y == "G", "" if y == "G" else y
    elif msg.endswith("D") and msg[:-1].isdigit():
        out["down"] = msg[:-1]
    has_ball = bool(out["down"]) or msg == "2PT"
    out["scorebug_message"] = "down_and_distance" if out["down"] else MESSAGES.get(msg, "")
    out["possession"] = {"green": "jets", "orange": "bengals"}.get(colour, "") if has_ball else ""
    out["distance"] = "goal" if out["goal"] else bucket_distance(int(out["yards"]), False) if out["yards"] else ""
    return out


def bucket_distance(yards, goal_to_go):
    if goal_to_go:
        return "goal"
    return "1-3" if yards <= 3 else "4-6" if yards <= 6 else "7-10" if yards <= 10 else "11+"


def bucket_yards_to_goal(y):
    return "80+" if y >= 80 else "50-79" if y >= 50 else "20-49" if y >= 20 else "10-19" if y >= 10 else "0-9"


def load_states(cfg):
    """One pre-snap state per pbp row of the quarter, in game order."""
    path = ROOT / cfg["game"]["pbp_path"]
    if not path.exists():
        raise SystemExit(f"{cfg['game']['pbp_path']} is missing (the maintainer provides it)")
    df = pd.read_csv(path, low_memory=False).copy()
    missing = [c for c in PBP_COLUMNS if c not in df.columns]
    if missing:
        raise SystemExit(f"pbp is missing columns: {', '.join(missing)}")
    home, away = cfg["game"]["home"], cfg["game"]["away"]
    # total_*_score is the score after the play; the scorebug before the snap shows the row above
    df["pre_home"] = df["total_home_score"].shift(1).fillna(0).astype(int)
    df["pre_away"] = df["total_away_score"].shift(1).fillna(0).astype(int)
    states = []
    for _, r in df[df["qtr"] == cfg["game"]["quarter"]].iterrows():
        has_down = pd.notna(r["down"])
        states.append({
            "play_id": int(r["play_id"]), "clock": r["time"], "clock_s": clock_s(r["time"]),
            "possession": TEAMS.get(r["posteam"], ""),
            "down": str(int(r["down"])) if has_down else "",
            "ydstogo": int(r["ydstogo"]) if has_down else None,
            "goal_to_go": bool(r["goal_to_go"]) if has_down and pd.notna(r["goal_to_go"]) else False,
            "yardline_100": int(r["yardline_100"]) if pd.notna(r["yardline_100"]) else None,
            f"{TEAMS[home]}_score": int(r["pre_home"]), f"{TEAMS[away]}_score": int(r["pre_away"]),
            f"{TEAMS[home]}_timeouts": int(r["home_timeouts_remaining"]),
            f"{TEAMS[away]}_timeouts": int(r["away_timeouts_remaining"]),
            "score_after": {TEAMS[home]: int(r["total_home_score"]), TEAMS[away]: int(r["total_away_score"])},
            "desc": str(r["desc"]),
        })
    return states


def state_view(s):
    """The state as the scorebug would print it, in the read's vocabulary."""
    return {"jets_score": str(s["jets_score"]), "bengals_score": str(s["bengals_score"]),
            "possession": s["possession"], "down": s["down"],
            "distance": "" if not s["down"] else "goal" if s["goal_to_go"] else str(s["ydstogo"]),
            "yardline_100": "" if s["yardline_100"] is None else str(s["yardline_100"]),
            "jets_timeouts": str(s["jets_timeouts"]), "bengals_timeouts": str(s["bengals_timeouts"])}


def cost(read, state):
    if read is None:
        return 0.0
    view = state_view(state)
    seen = {"jets_score": read["jets_score"], "bengals_score": read["bengals_score"], "possession": read["possession"],
            "down": read["down"], "distance": "goal" if read["goal"] else read["yards"]}
    c = sum(w for field, w in WEIGHTS.items() if seen[field] and seen[field] != view[field])
    rs = clock_s(read.get("clock"))
    if rs is not None and state["clock_s"] is not None:
        c += 0.5 * min(1.0, abs(rs - state["clock_s"]) / 60)
    return c


def align(reads, states):
    """Monotone alignment by dynamic programming. Returns one state index per frame."""
    n, m = len(reads), len(states)
    total = [[0.0] * m for _ in range(n)]
    back = [[0] * m for _ in range(n)]
    for i in range(n):
        best, best_j = float("inf"), 0
        for j in range(m):
            if i and total[i - 1][j] < best:
                best, best_j = total[i - 1][j], j
            total[i][j] = cost(reads[i], states[j]) + (best if i else 0.0)
            back[i][j] = best_j
    j = min(range(m), key=lambda k: total[n - 1][k])
    path = [0] * n
    for i in range(n - 1, -1, -1):
        path[i] = j
        j = back[i][j]
    return path


def print_plays(states):
    for s in states:
        print(f"{s['play_id']:>5}  {s['clock']:>5}  {s['possession'] or '-':8} "
              f"{s['down'] or '-'}&{s['ydstogo'] if s['ydstogo'] is not None else '-':<3} "
              f"NYJ {s['score_after']['jets']:>2} CIN {s['score_after']['bengals']:>2}  {s['desc'][:110]}")
    print(f"{len(states)} rows")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plays", action="store_true")
    args = ap.parse_args()
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    states = load_states(cfg)
    if args.plays:
        print_plays(states)
        return

    with (ROOT / "data" / "frames_index.csv").open(encoding="utf-8", newline="") as fh:
        frames = sorted(csv.DictReader(fh), key=lambda r: float(r["t_actual"]))
    with (LABELS / "scorebug_reads_draft.csv").open(encoding="utf-8", newline="") as fh:
        by_frame = {r["frame"]: {k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(fh)}
    reads = []
    for f in frames:
        r = by_frame.get(f["frame"])
        reads.append(parse_read(r) if r and r.get("scorebug_visible") == "yes" else None)
    path = align(reads, states)

    rows, disagreements = [], []
    for f, read, j in zip(frames, reads, path):
        s, view = states[j], state_view(states[j])
        row = {"frame": f["frame"], "t_actual": f["t_actual"], "scorebug_visible": "yes" if read else "no",
               "state_idx": j, "play_id": s["play_id"], "pbp_clock": s["clock"],
               "pbp_yardline_100": "" if s["yardline_100"] is None or not s["down"] else s["yardline_100"],
               **{q: "" for q in B_QUESTIONS}}
        if read:
            seen = clock_s(read.get("clock"))
            row.update({
                "quarter": read["quarter"] if read["quarter"].isdigit() else "not_visible",
                "clock_minutes": str(seen // 60) if seen is not None else "not_visible",
                "jets_score": read["jets_score"] or "not_visible",    # blank read = points animation ("+6")
                "bengals_score": read["bengals_score"] or "not_visible",
                "scorebug_message": read["scorebug_message"], "panel_colour": read["panel_colour"],
                "possession": read["possession"] or "not_shown",
                "down": read["down"] or "not_shown", "distance": read["distance"] or "not_shown",
                "jets_timeouts": read["jets_timeouts"], "bengals_timeouts": read["bengals_timeouts"],
                "review_banner": read["banner"] if read["banner"] in REVIEW_BANNERS else "none",
            })
            checks = {"jets_score": read["jets_score"], "bengals_score": read["bengals_score"],
                      "possession": read["possession"], "down": read["down"],
                      "distance": "goal" if read["goal"] else read["yards"],
                      "jets_timeouts": read["jets_timeouts"], "bengals_timeouts": read["bengals_timeouts"]}
            for field, value in checks.items():
                if value and value != view[field]:
                    disagreements.append({"frame": f["frame"], "t_actual": f["t_actual"], "field": field,
                                          "read": value, "pbp": view[field], "play_id": s["play_id"],
                                          "pbp_clock": s["clock"], "desc": s["desc"][:120]})
            # the frame's clock should sit between the previous snap and the next one
            earlier = states[j - 1]["clock_s"] if j else None
            later = states[j + 1]["clock_s"] if j + 1 < len(states) else None
            if seen is not None and ((earlier is not None and seen > earlier) or (later is not None and seen < later)):
                disagreements.append({"frame": f["frame"], "t_actual": f["t_actual"], "field": "clock",
                                      "read": read["clock"], "pbp": s["clock"], "play_id": s["play_id"],
                                      "pbp_clock": s["clock"], "desc": s["desc"][:120]})
        rows.append(row)

    with (LABELS / "labels_B.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with (LABELS / "disagreements.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["frame", "t_actual", "field", "read", "pbp", "play_id", "pbp_clock", "desc"])
        w.writeheader()
        w.writerows(disagreements)
    hidden = sum(r is None for r in reads)
    print(f"{len(frames)} frames aligned to {len(set(path))} of {len(states)} pbp states; "
          f"scorebug hidden on {hidden} ({100 * hidden / len(frames):.0f}%); "
          f"{len(disagreements)} disagreements on {len({d['frame'] for d in disagreements})} frames")
    print("wrote data/labels/labels_B.csv and data/labels/disagreements.csv")


if __name__ == "__main__":
    main()
