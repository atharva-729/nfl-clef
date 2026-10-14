"""Phase 4: RECORD. Ask Clef the schema questions about every frame, cache-first.

  python src/run_clef.py --dry-run     # count frames, project cost, change nothing
  python src/run_clef.py               # real run (refuses if projected cost > BUDGET_USD)

Phase 7 variants (same cache and budget rules):
  --flash                 use the smaller model in config.yaml `model_flash`
  --frames-dir DIR        read frames from DIR instead of data/frames
  --context               add the previous frame's trusted answers to the state text

Frames are data/frames/f_NNNN.png with their times in data/frames_index.csv (t_actual). If
`image.scorebug_box` is set in config.yaml, that region is sent as a second, full-resolution image.

Cache key = SHA-256(frame bytes + schema version + state text + model id + crop box). A cache hit
never calls the API. Output goes to runs/<run_id>/ (raw/, answers.jsonl, run_meta.json).
"""
import argparse
import csv
import hashlib
import json
import os
import statistics
import time
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

from clef_client import ask, load_config, load_schema, parse_answers

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "runs" / "cache"
FALLBACK_TOKENS = 7000  # Phase 1 measured about 6,800 input tokens per frame with the crop (docs/api_notes.md)
MAX_TRIES = 3
NO_INFO = {"cant_tell", "not_visible", "none"}


def cache_key(frame, schema, model, state, crop=None):
    h = hashlib.sha256()
    h.update(Path(frame).read_bytes())
    h.update(str(schema["version"]).encode())
    h.update(state.encode())
    h.update(model.encode())
    if crop:
        h.update(repr(list(crop)).encode())
    return h.hexdigest()


def tokens_per_call():
    """Prefer the real figure from Phase 0 or any cached response; else the documented one."""
    for p in [ROOT / "runs" / "phase0" / "response.json", *sorted(CACHE.glob("*.json"))[:1]]:
        try:
            return json.loads(p.read_text(encoding="utf-8"))["usage"]["input_tokens"]
        except (OSError, KeyError, ValueError):
            continue
    return FALLBACK_TOKENS


def context_state(state, prev, min_conf):
    """State text plus the previous frame's trusted, informative answers (Phase 7 context ablation)."""
    if prev is None:
        return state
    kept = [f"{qid}={a['answer']}" for qid, a in prev["answers"].items()
            if a["confidence"] >= min_conf and a["answer"] not in NO_INFO]
    if not kept:
        return state
    return (f"{state}\n\nFor reference, the answers given for the previous sampled frame were: {', '.join(kept)}. "
            "The picture may have changed since then; answer from this frame.")


def ask_with_retry(frame, state, schema, model_cfg, crop=None):
    for attempt in range(1, MAX_TRIES + 1):
        try:
            return ask(frame, state, schema["questions"], model=model_cfg["id"], provider=model_cfg.get("provider"),
                       crop=crop)
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            if code not in (429, 500, 502, 503, 504) or attempt == MAX_TRIES:
                raise
        except httpx.TransportError:
            if attempt == MAX_TRIES:
                raise
        time.sleep(2 ** attempt)


def validate(answers, schema):
    """Return a list of problems: missing questions, unknown options, bad probabilities."""
    problems = []
    for q in schema["questions"]:
        a = answers.get(q["id"])
        if a is None:
            problems.append(f"{q['id']}: missing")
            continue
        if set(a["probs"]) != set(q["options"]):
            problems.append(f"{q['id']}: options differ from schema")
        elif abs(sum(a["probs"].values()) - 1) > 0.01:
            problems.append(f"{q['id']}: probabilities sum to {sum(a['probs'].values()):.3f}")
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--run-id", default=datetime.now().strftime("run_%Y%m%d_%H%M%S"))
    ap.add_argument("--flash", action="store_true")
    ap.add_argument("--frames-dir", default=str(ROOT / "data" / "frames"))
    ap.add_argument("--context", action="store_true")
    args = ap.parse_args()

    load_dotenv(ROOT / ".env")
    budget = float(os.environ.get("BUDGET_USD", "5.00"))
    cfg = load_config()
    schema = load_schema()
    model_cfg = cfg["model_flash"] if args.flash else cfg["model"]
    model = model_cfg["id"]
    price = model_cfg["price_per_m_input_usd"]
    min_conf = cfg["events"]["min_conf"]

    crop = cfg["image"].get("scorebug_box")
    frames = sorted(Path(args.frames_dir).glob("f_*.png"))
    if not frames:
        raise SystemExit(f"no frames in {args.frames_dir} (capture them first, see docs/SOURCE.md)")
    with (ROOT / "data" / "frames_index.csv").open(encoding="utf-8", newline="") as fh:
        times = {r["frame"]: float(r["t_actual"]) for r in csv.DictReader(fh)}
    if args.context:  # the state depends on the previous answer, so cache hits are not known in advance
        todo = frames
    else:
        todo = [f for f in frames if not (CACHE / f"{cache_key(f, schema, model, schema['state'], crop)}.json").exists()]
    tokens = tokens_per_call()
    projected = len(todo) * tokens * price / 1e6

    print(f"model {model}, schema v{schema['version']}, {len(schema['questions'])} questions, scorebug crop {'on' if crop else 'off'}")
    print(f"frames {len(frames)}, cached {len(frames) - len(todo)}, to call {'up to ' if args.context else ''}{len(todo)}")
    print(f"~{tokens} input tokens per call -> projected cost ${projected:.4f} (budget ${budget:.2f})")
    if projected > budget:
        raise SystemExit("REFUSING: projected cost exceeds BUDGET_USD")
    if args.dry_run:
        print("dry run: no API calls made")
        return

    run_dir = ROOT / "runs" / args.run_id
    (run_dir / "raw").mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)

    started = time.time()
    spent, latencies, hits, failures, rows = 0.0, [], 0, [], []
    for i, frame in enumerate(frames):
        state = context_state(schema["state"], rows[-1] if rows else None, min_conf) if args.context else schema["state"]
        cache_file = CACHE / f"{cache_key(frame, schema, model, state, crop)}.json"
        if cache_file.exists():
            response = json.loads(cache_file.read_text(encoding="utf-8"))
            hits += 1
        else:
            if spent >= budget:
                failures.append((frame.name, "stopped: budget reached"))
                continue
            try:
                response = ask_with_retry(frame, state, schema, model_cfg, crop)
            except Exception as e:  # keep going; failures are reported at the end
                failures.append((frame.name, str(e)[:300]))
                print(f"[{i + 1}/{len(frames)}] {frame.name} FAILED: {str(e)[:120]}")
                continue
            cache_file.write_text(json.dumps(response), encoding="utf-8")
            spent += response["usage"].get("cost", 0) or 0
            latencies.append(response["_meta"]["latency_s"])
            print(f"[{i + 1}/{len(frames)}] {frame.name} ok {response['_meta']['latency_s']}s")
        (run_dir / "raw" / f"{frame.stem}.json").write_text(json.dumps(response, indent=2), encoding="utf-8")
        answers = parse_answers(response)
        problems = validate(answers, schema)
        if problems:
            failures.append((frame.name, "; ".join(problems)))
        rows.append({"frame": frame.name, "t_seconds": times[frame.name], "answers": answers})

    (run_dir / "answers.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    meta = {
        "run_id": args.run_id, "model": model, "provider": model_cfg.get("provider"),
        "schema_version": schema["version"], "scorebug_crop": crop, "frames_dir": Path(args.frames_dir).name, "context": args.context,
        "frames": len(frames), "answered": len(rows),
        "cache_hits": hits, "new_calls": len(latencies), "failures": failures,
        "cost_usd_this_run": round(spent, 6), "elapsed_s": round(time.time() - started, 1),
        "latency_median_s": round(statistics.median(latencies), 3) if latencies else None,
        "latency_p95_s": round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)], 3) if latencies else None,
    }
    (run_dir / "run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
