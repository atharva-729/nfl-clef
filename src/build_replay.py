"""Phase 7: build the replay page from a recorded run. No API calls.

  python src/build_replay.py [runs/<run_id>]     # default: the latest run
  python -m http.server 8010                     # from the repo root
  open http://localhost:8010/replay/

The NFL has disabled embedding for this video (docs/SOURCE.md), so the page is the README's
fallback: a flipbook of the captured frames on the video's own clock, with an "open on YouTube at
this moment" link. Run detect_events.py first; the page reads only files.

The story page (index.html at the repo root) plays the hosted Q4 video file instead, with Clef's
answers drawn over it. For that, each frame also carries its time in that file (data/video_sync.csv
from align_video.py), Clef's latency on it, and any grey-area questions (data/labels/grey_areas.csv);
each event carries the time its streak completed, when a live system could first report it.

Writes replay/timeline.json (per frame: time, answers, confidences, labels; plus events),
replay/index.html and replay/frames/*.jpg (1280x720 copies of the frames: NFL footage, git-ignored).
"""
import argparse
import csv
import json
from pathlib import Path

from PIL import Image

from clef_client import load_config, load_schema

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "replay"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", nargs="?")
    args = ap.parse_args()
    runs = sorted(p for p in (ROOT / "runs").glob("run_*") if (p / "events.json").exists())
    run_dir = Path(args.run_dir) if args.run_dir else (runs[-1] if runs else None)
    if run_dir is None or not (run_dir / "events.json").exists():
        raise SystemExit("no run with events.json found (run src/detect_events.py first)")
    cfg = load_config()
    schema = load_schema()
    meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
    ev = json.loads((run_dir / "events.json").read_text(encoding="utf-8"))
    cost = sum(json.loads(f.read_text(encoding="utf-8")).get("usage", {}).get("cost", 0) or 0
               for f in (run_dir / "raw").glob("*.json"))
    with (ROOT / "data/labels/labels.csv").open(encoding="utf-8", newline="") as fh:
        labels = {r["frame"]: r for r in csv.DictReader(fh)}
    with (ROOT / "data/labels/events_truth.csv").open(encoding="utf-8", newline="") as fh:
        truth_events = [{"event": r["event"], "subtype": r["subtype"], "t": float(r["t_video"])}
                        for r in csv.DictReader(fh) if r["t_video"].strip()]
    sync = read_csv(ROOT / "data/video_sync.csv", "frame")
    grey = {}
    for r in read_csv(ROOT / "data/labels/grey_areas.csv").values():
        grey.setdefault(r["frame"], {})[r["question"]] = r["reason"]

    (OUT / "frames").mkdir(parents=True, exist_ok=True)
    frames = []
    for line in (run_dir / "answers.jsonl").read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        row = json.loads(line)
        lab = labels.get(row["frame"], {})
        jpg = OUT / "frames" / (Path(row["frame"]).stem + ".jpg")
        if not jpg.exists():
            Image.open(ROOT / "data/frames" / row["frame"]).convert("RGB").resize((1280, 720), Image.LANCZOS).save(jpg, quality=82)
        raw = json.loads((run_dir / "raw" / (Path(row["frame"]).stem + ".json")).read_text(encoding="utf-8"))
        extra = {"v": float(sync[row["frame"]]["t_file"])} if row["frame"] in sync else {}
        if raw.get("_meta", {}).get("latency_s"):
            extra["lat"] = round(raw["_meta"]["latency_s"], 2)
        if row["frame"] in grey:
            extra["grey"] = grey[row["frame"]]
        frames.append({"t": row["t_seconds"], "img": "frames/" + jpg.name, **extra,
                       "truth": {q["id"]: lab[q["id"]] for q in schema["questions"] if (lab.get(q["id"]) or "").strip()},
                       "answers": {qid: {"a": a["answer"], "c": round(a["confidence"], 3)} for qid, a in row["answers"].items()}})
    frames.sort(key=lambda f: f["t"])

    timeline = {
        "meta": {"run_id": meta["run_id"], "model": meta["model"], "schema_version": meta["schema_version"],
                 "cost_usd": round(cost, 4), "frames": len(frames), "sample_interval_s": cfg["sample_interval_s"],
                 "start_s": cfg["video"]["q4_start_video_s"], "end_s": frames[-1]["t"] + cfg["sample_interval_s"],
                 "min_conf": ev["min_conf"], "persist_frames": ev["persist_frames"], "video_id": cfg["video"]["id"],
                 "soft_labels": schema.get("soft_labels", []), "video_file_url": cfg["video"].get("file_url")},
        "frames": frames,
        "events": [{"event": e["event"], "subtype": e["subtype"], "t": e["t_seconds"], "known_t": e.get("confirmed_t_seconds", e["t_seconds"]),
                    "evidence": e["evidence"]}
                   for e in ev["events"]],
        "truth_events": truth_events,
        # for the story page (index.html at the repo root)
        "scoring": ev["scoring"], "false_positives": ev["false_positives"], "sensitivity": ev["sensitivity"],
        "metrics": json.loads((run_dir / "metrics.json").read_text(encoding="utf-8")) if (run_dir / "metrics.json").exists() else None,
    }
    (OUT / "timeline.json").write_text(json.dumps(timeline), encoding="utf-8")
    (OUT / "index.html").write_text(PAGE, encoding="utf-8")
    print(f"wrote replay/index.html, replay/timeline.json and replay/frames/ from {run_dir.name} "
          f"({len(frames)} frames, {len(timeline['events'])} events)")


def read_csv(path, key=None):
    """Rows of a CSV keyed by `key` (or by position); {} if the file does not exist."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as fh:
        return {(r[key] if key else n): r for n, r in enumerate(csv.DictReader(fh))}


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Clef NFL replay</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#111418;--panel:#1b2027;--line:#2c343f;--fg:#e6e9ee;--mut:#8b95a3;--acc:#4da3ff;--ok:#3ecf8e;--warn:#f5a524;--bad:#ff5c5c;--jets:#12714d;--cin:#e4531b}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--fg);font:14px system-ui,sans-serif}
#app{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(340px,1fr);gap:14px;padding:14px;max-width:1600px;margin:0 auto}
@media(max-width:900px){#app{grid-template-columns:1fr}}
#frame{width:100%;aspect-ratio:16/9;background:#000;border-radius:6px;display:block;object-fit:contain}
#ctl{display:flex;gap:8px;align-items:center;margin-top:8px;flex-wrap:wrap}
button,select{background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:6px 12px;font:inherit;cursor:pointer}
button:hover{border-color:var(--acc)} #clock{font-variant-numeric:tabular-nums;color:var(--mut)} #yt{margin-left:auto;color:var(--acc)}
.strip{position:relative;height:30px;margin-top:8px;background:var(--panel);border:1px solid var(--line);border-radius:6px;cursor:pointer}
#moments{height:14px} .head{position:absolute;top:0;bottom:0;width:2px;background:#fff;pointer-events:none}
.mk{position:absolute;top:3px;bottom:3px;width:4px;margin-left:-2px;border-radius:2px;background:var(--acc)}
.mk.minor{background:var(--mut);top:10px;bottom:10px} .mk.true{background:none;border:1px dashed var(--ok);display:none}
body.truth .mk.true{display:block} .seg{position:absolute;top:2px;bottom:2px;background:var(--warn);border-radius:2px;min-width:2px}
.legend{color:var(--mut);font-size:12px;margin-top:6px}
#side{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:12px 14px;min-width:0}
h3{margin:14px 0 8px;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--acc)} h3:first-child{margin-top:0}
#live{float:right;color:var(--mut);font-size:12px;text-transform:none;letter-spacing:0}
#bug{display:grid;grid-template-columns:1fr auto 1fr;border-radius:6px;overflow:hidden;margin-bottom:8px;font-weight:700}
#bug div{padding:8px 12px} #bug .j{background:var(--jets)} #bug .c{background:var(--cin);text-align:right}
#bug .m{background:#000;text-align:center;font-weight:600;min-width:130px} #bug .m small{display:block;font-weight:400;color:#ccc}
#bug b{font-size:24px;font-variant-numeric:tabular-nums;margin:0 8px}
.row{display:grid;grid-template-columns:128px 1fr 44px;gap:8px;align-items:center;padding:4px 6px;border-radius:4px}
.row .k{color:var(--mut)} .row .v{font-weight:600;overflow-wrap:anywhere} .row .c{text-align:right;color:var(--mut);font-variant-numeric:tabular-nums}
.bar{grid-column:1/-1;height:3px;background:#2a323d;border-radius:2px} .bar i{display:block;height:100%;border-radius:2px;background:var(--ok)}
.row.low .v{color:var(--mut);font-weight:400} .row.low .bar i{background:var(--warn)}
.lab{display:none;font-weight:400;font-size:12px}
body.truth .row.wrong{background:rgba(255,92,92,.14)} body.truth .row.wrong .v{color:var(--bad)} body.truth .row.wrong .lab{display:block;color:var(--fg)}
#log{max-height:230px;overflow:auto} .ev{padding:5px 8px;border-radius:4px;cursor:pointer;display:flex;gap:8px}
.ev:hover{background:#2a323d} .ev .t{margin-left:auto;color:var(--mut);font-variant-numeric:tabular-nums} .ev small{color:var(--mut)}
.empty{color:var(--mut);padding:6px 8px} label{cursor:pointer;user-select:none}
#foot{color:var(--mut);font-size:12px;margin-top:14px;line-height:1.5} #msg{color:var(--mut);font-size:12px;margin-top:6px}
</style></head><body>
<div id="app">
 <div>
  <img id="frame" alt="captured frame">
  <div id="ctl"><button id="play">Play</button>
   <select id="speed"><option value="1">1x (real time)</option><option value="4" selected>4x</option><option value="12">12x</option></select>
   <span id="clock"></span><a id="yt" target="_blank" rel="noopener">Open on YouTube at this moment &#8599;</a></div>
  <div class="strip" id="strip"><div class="head"></div></div>
  <div class="strip" id="moments"><div class="head"></div></div>
  <div class="legend">Top strip: blue = detected event, grey = first down, dashed green (ground truth on) = true event.
   Lower strip: moments Clef marked highlight-worthy. Click anywhere to jump.</div>
  <div id="msg">Loading&hellip;</div>
 </div>
 <div id="side">
  <h3>Now <span id="live"></span></h3>
  <div id="bug"></div><div id="now"></div>
  <h3>Events</h3><div id="log"></div>
  <h3>Sponsor exposure so far *</h3><div id="sponsor"></div>
  <h3>Ground truth</h3>
  <label><input type="checkbox" id="truth"> Show ground truth (answers that disagree with the labels turn red)</label>
  <div id="score" style="color:var(--mut);margin-top:4px"></div>
  <div id="foot"></div>
 </div>
</div>
<script>
const $ = id => document.getElementById(id);
const NOW = [['possession', 'Possession'], ['down', 'Down'], ['distance', 'Distance'], ['scorebug_message', 'Scorebug message'],
  ['review_banner', 'Review banner'], ['jets_timeouts', 'Jets timeouts'], ['bengals_timeouts', 'Bengals timeouts'],
  ['main_view', 'Main view'], ['play_phase', 'Play phase'], ['replay_showing', 'Replay'], ['info_overlay', 'Info overlay']];
const EVENTS = {touchdown_jets: ['🏈', 'Touchdown, Jets'], touchdown_bengals: ['🏈', 'Touchdown, Bengals'], field_goal_jets: ['🥅', 'Field goal, Jets'],
  field_goal_bengals: ['🥅', 'Field goal, Bengals'], extra_point_good: ['✅', 'Extra point good'], two_point_good: ['✌️', 'Two-point conversion good'],
  two_point_attempt: ['🎯', 'Two-point attempt'], conversion_no_good: ['❌', 'Conversion no good'], tie: ['🤝', 'Tie game'], lead_change: ['🔀', 'Lead change'],
  possession_change: ['🔄', 'Possession change'], first_down: ['▶️', 'First down'], timeout_jets: ['⏱️', 'Timeout, Jets'], timeout_bengals: ['⏱️', 'Timeout, Bengals'],
  penalty: ['🚩', 'Penalty flag'], replay_review: ['🔍', 'Replay review'], game_end: ['🏁', 'Game end']};
const MINOR = new Set(['first_down']);
const hms = t => `${Math.floor(t / 3600)}:${String(Math.floor(t % 3600 / 60)).padStart(2, '0')}:${String(Math.floor(t % 60)).padStart(2, '0')}`;
const nice = v => v.replace(/_/g, ' ');
const evName = e => (EVENTS[e.event] || ['•', e.event])[1] + (e.subtype ? ` (${nice(e.subtype)})` : '');
let T, M, t = 0, shown = -1, logged = -1, playing = false, last = 0;

function frameAt(x) { let i = 0; while (i + 1 < T.frames.length && T.frames[i + 1].t <= x) i++; return i; }
const pos = x => `${Math.max(0, Math.min(100, (x - M.start_s) / (M.end_s - M.start_s) * 100))}%`;
const ans = (f, q) => f.answers[q] || {a: '?', c: 0};

function renderNow(i) {
  const f = T.frames[i], soft = new Set(M.soft_labels);
  $('frame').src = f.img;
  $('live').textContent = `frame ${i + 1} of ${T.frames.length}`;
  const js = ans(f, 'jets_score'), bs = ans(f, 'bengals_score'), q = ans(f, 'quarter'), cm = ans(f, 'clock_minutes');
  const num = a => /^\d+$/.test(a.a) ? a.a : '–';
  $('bug').innerHTML = `<div class="j">JETS <b>${num(js)}</b></div>
    <div class="m">${ans(f, 'scorebug_visible').a === 'yes' ? `Q${num(q)} &middot; ${num(cm)}:xx<small>clock minute only</small>` : 'scorebug not visible'}</div>
    <div class="c"><b>${num(bs)}</b> BENGALS</div>`;
  let wrong = 0, scored = 0;
  const rows = [['jets_score', 'Jets score'], ['bengals_score', 'Bengals score'], ['clock_minutes', 'Clock minutes'], ...NOW];
  $('now').innerHTML = rows.map(([qid, name]) => {
    const a = ans(f, qid), lab = f.truth[qid], bad = lab !== undefined && lab !== a.a;
    if (lab !== undefined) { scored++; wrong += bad; }
    return `<div class="row${a.c < M.min_conf ? ' low' : ''}${bad ? ' wrong' : ''}"><span class="k">${name}${soft.has(qid) ? ' *' : ''}</span>
      <span class="v">${nice(a.a)}<span class="lab">label: ${bad ? nice(lab) : ''}</span></span>
      <span class="c">${Math.round(a.c * 100)}%</span><div class="bar"><i style="width:${a.c * 100}%"></i></div></div>`;
  }).join('');
  $('score').textContent = `This frame: ${scored - wrong} of ${scored} shown answers match the labels.`;
  const by = {};
  for (let k = 0; k <= i; k++) {
    const s = ans(T.frames[k], 'sponsor_visible'), p = ans(T.frames[k], 'sponsor_placement');
    if (s.a === 'yes' && s.c >= M.min_conf) by[p.a] = (by[p.a] || 0) + M.sample_interval_s;
  }
  const total = Object.values(by).reduce((a, b) => a + b, 0);
  $('sponsor').innerHTML = `<div class="row"><span class="k">Total</span><span class="v">${total} s</span><span class="c"></span></div>` +
    Object.entries(by).sort((a, b) => b[1] - a[1]).map(([p, s]) => `<div class="row"><span class="k">${nice(p)}</span><span class="v">${s} s</span><span class="c"></span></div>`).join('');
}

function renderLog() {
  const seen = T.events.filter(e => e.t <= t);
  if (seen.length === logged) return;
  logged = seen.length;
  $('log').innerHTML = seen.length ? seen.slice().reverse().map(e => `<div class="ev" data-t="${e.t}"><span>${(EVENTS[e.event] || ['•'])[0]}</span>
    <span>${evName(e)}<br><small>${e.evidence.question}: ${e.evidence.from} &rarr; ${e.evidence.to}</small></span><span class="t">${hms(e.t)}</span></div>`).join('')
    : '<div class="empty">No events detected yet.</div>';
  $('log').querySelectorAll('.ev').forEach(d => d.onclick = () => seek(+d.dataset.t));
}

function render() {
  document.querySelectorAll('.head').forEach(h => h.style.left = pos(t));
  $('clock').textContent = `video ${hms(t)}`;
  $('yt').href = `https://www.youtube.com/watch?v=${M.video_id}&t=${Math.floor(t)}s`;
  const i = frameAt(t);
  if (i !== shown) { shown = i; renderNow(i); }
  renderLog();
}
function seek(x) { t = Math.max(M.start_s, Math.min(M.end_s, x)); logged = -1; render(); }
function loop(now) {
  if (playing) {
    t += (now - last) / 1000 * +$('speed').value;
    if (t >= M.end_s) { t = M.end_s; playing = false; $('play').textContent = 'Play'; }
    render();
  }
  last = now;
  requestAnimationFrame(loop);
}

function setup() {
  M = T.meta; t = M.start_s;
  $('strip').insertAdjacentHTML('beforeend',
    T.events.map(e => `<div class="mk${MINOR.has(e.event) ? ' minor' : ''}" style="left:${pos(e.t)}" title="${evName(e)} ${hms(e.t)}"></div>`).join('') +
    T.truth_events.map(e => `<div class="mk true" style="left:${pos(e.t)}" title="true: ${evName(e)} ${hms(e.t)}"></div>`).join(''));
  let segs = '', start = null;
  T.frames.forEach((f, i) => {
    const a = ans(f, 'highlight_worthy'), on = a.a === 'yes' && a.c >= M.min_conf;
    if (on && start === null) start = f.t;
    if ((!on || i === T.frames.length - 1) && start !== null) {
      segs += `<div class="seg" style="left:${pos(start)};width:calc(${pos(f.t)} - ${pos(start)})" title="highlight ${hms(start)}"></div>`; start = null;
    }
  });
  $('moments').insertAdjacentHTML('beforeend', segs);
  document.querySelectorAll('.strip').forEach(s => s.onclick = e => {
    const r = s.getBoundingClientRect(); seek(M.start_s + (e.clientX - r.left) / r.width * (M.end_s - M.start_s)); });
  $('play').onclick = () => { if (t >= M.end_s) seek(M.start_s); playing = !playing; $('play').textContent = playing ? 'Pause' : 'Play'; };
  $('truth').onchange = () => document.body.classList.toggle('truth', $('truth').checked);
  addEventListener('keydown', e => { if (e.key === 'ArrowRight') seek(t + M.sample_interval_s); if (e.key === 'ArrowLeft') seek(t - M.sample_interval_s);
    if (e.key === ' ') { e.preventDefault(); $('play').click(); } });
  $('foot').innerHTML = `Model <b>${M.model}</b> &middot; schema v${M.schema_version} &middot; run ${M.run_id} &middot; ${M.frames} frames, one every
    ${M.sample_interval_s} s &middot; cost of this run $${M.cost_usd.toFixed(2)}<br>
    Recorded once and replayed from files; no model is called while this page plays. An answer below ${Math.round(M.min_conf * 100)}% confidence is shown
    dimmed and cannot trigger an event. * = soft labels or questions scored against them (indicative only; sponsor figures especially).<br>
    The NFL has disabled embedding for this video, so this page steps through captured still frames; use the link under the picture to watch the moment on YouTube.<br>
    Video &copy; NFL (New York Jets at Cincinnati Bengals, 2025 Week 8). Play-by-play: nflverse. <b>Internal &mdash; not for distribution.</b>`;
  const want = new URLSearchParams(location.hash.slice(1));
  if (want.get('truth')) { $('truth').checked = true; document.body.classList.add('truth'); }
  if (want.get('t')) t = +want.get('t');
  $('msg').textContent = 'Space = play/pause, left/right arrows = step one frame.';
  render();
  requestAnimationFrame(loop);
}

fetch('timeline.json').then(r => r.json()).then(x => { T = x; setup(); })
  .catch(e => { $('msg').textContent = 'Could not load timeline.json. Serve the repo root with: python -m http.server 8010 (' + e + ')'; });
</script></body></html>
"""

if __name__ == "__main__":
    main()
