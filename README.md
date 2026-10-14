# Clef NFL Probe *(private, internal only)*

**Can a vision decision model watch an NFL broadcast and tell us, frame by frame, what is happening — well enough to drive things a team's media and partnership people would pay for?**

This is an offshoot of **`clef-video-probe`** (the NASA Webb launch demo). Same idea, same pipeline, now on American football, because the client is an NFL team and needs to see it working on *their* sport:

1. capture still frames from an NFL broadcast at a fixed interval,
2. ask **Cloudflare's Clef** a fixed set of multiple-choice questions about each frame,
3. score the answers against ground truth built from the **official play-by-play**,
4. turn changes in the answers into **detected events** (touchdown, conversion, lead change, turnover, timeout, review…),
5. record once, then **replay** in sync with the YouTube video so it looks live — tokens spent once.

> **For Claude Code:** this README is your work plan. Work through the phases **in order**. At the end of every phase, **stop at the checkpoint**, print the checkpoint report, and **wait for the maintainer to reply "go"**. Read [Rules for Claude Code](#rules-for-claude-code) and [Usage & compliance](#usage--compliance) before doing anything.

## Run the demo

Needs only Python 3 and Chrome (plus internet: the video streams from its hosted copy). Nothing to install.

```
git clone https://github.com/atharva-729/nfl-clef.git
cd nfl-clef
python -m http.server 8010
```

Open http://localhost:8010/ in Chrome and scroll to **Watch it work**. Keys: **Space** play, **F** fullscreen,
**T** check against our labels, **O** overlay on/off, **I** hide the strip, **←/→** ±5 s in fullscreen.
The page must be served (not opened as a file) so it can load `replay/timeline.json`.

---

## Contents

- [The game](#the-game)
- [What carries over from clef-video-probe](#what-carries-over-from-clef-video-probe)
- [How it works](#how-it-works)
- [Repo layout](#repo-layout)
- [The question schema](#the-question-schema)
- [Logic](#logic)
- [Phases](#phases)
- [Rules for Claude Code](#rules-for-claude-code)
- [Usage & compliance](#usage--compliance)
- [Business angle](#business-angle)
- [Credits](#credits)

---

## The game

| | |
|---|---|
| Matchup | **New York Jets at Cincinnati Bengals**, 2025 season, Week 8 |
| Date | 26 Oct 2025 |
| nflverse `game_id` | `2025_08_NYJ_CIN` |
| Final | **NYJ 39 – CIN 38** |
| Video | NFL's official YouTube upload: *"Game of the Week! New York Jets vs. Cincinnati Bengals FULL GAME \| NFL 2025 Season Week 8"* — https://youtu.be/DG5G2uif0DU (video id `DG5G2uif0DU`) |
| Window | **The whole 4th quarter** — from the Q4 opening snap to the final kneel-down |
| Ground truth | `data/pbp/nyj_cin_2025_w8.csv` (nflverse play-by-play for this game, 173 rows) |

### Why the 4th quarter

| Qtr | Plays | TDs | FGs | 2-pt tries | Timeouts | 4th-down tries | Score after (NYJ–CIN) |
|---|---|---|---|---|---|---|---|
| 1 | 35 | 1 | 1 | 0 | 0 | 0 | 0–10 |
| 2 | 51 | 3 | 2 | 0 | 3 | 0 | 13–24 |
| 3 | 38 | 1 | 1 | 0 | 0 | 0 | 16–31 |
| **4** | **44** | **4** | 0 | **2** | 1 | **2** | **39–38** |

Q4 is a 15-point Jets comeback — almost every event type fires, and it's the client's win:

| Game clock | What happens | Score after (NYJ–CIN) |
|---|---|---|
| 15:00 | Q4 starts | 16–31 |
| 14:21 | Jets TD (B. Hall 5-yd run) | 22–31 |
| 14:17 | Jets 2-pt conversion **good** | 24–31 |
| 10:24 | Bengals TD (C. Brown 1-yd run), **replay review, upheld**; XP | 24–38 |
| 8:01 | Jets TD (B. Hall 27-yd run) | 30–38 |
| 7:52 | Jets 2-pt try, **replay review** — pbp text says "fails" but the score columns show it counting (32–38 by 2:28). **Verify against the broadcast.** | 32–38 |
| 2:00 | Jets TD (B. Hall pass to M. Taylor) — **tie** | 38–38 |
| 1:54 | Jets XP good — **lead change** | 39–38 |
| 0:37 | Bengals timeout #1 | 39–38 |
| 0:37–0:32 | Two incompletions → **turnover on downs** | 39–38 |
| 0:27 | Jets kneel — game over | 39–38 |

Expected size: Q4 is ~22–25 min of video → **~270–300 frames** at 5 s.

---

## What carries over from clef-video-probe

The sibling repo lives at `../clef-video-probe` (override with `CLEF_PROBE_PATH` in `.env`). **Read its README and code first, and reuse — don't rewrite.**

| Copy from `clef-video-probe` | Change needed here |
|---|---|
| `src/clef_client.py` | none expected (API shape already confirmed in its `docs/api_notes.md`) |
| `docs/api_notes.md` | copy as-is; append anything new learned |
| `src/run_clef.py` (dry run, budget cap, cache, retries) | add `--with-captions` mode (Phase 8) |
| `src/evaluate.py` | schema-driven; add exact-integer and per-team metrics |
| `src/detect_events.py` (smoothing + transitions) | new football event rules (below) |
| `src/build_replay.py` + replay page | video source becomes the **YouTube IFrame player**, not a local mp4 |
| `data/labels/label_helper.html` | add a "draft vs reviewed" view |
| `.env.example`, `requirements.txt`, `.gitignore` conventions | add `playwright` |

Do **not** copy rocket data, frames, labels, runs, or reports. Record in `docs/PROVENANCE.md` which files were copied from which commit of `clef-video-probe` and what changed.

---

## How it works

```
YouTube (embedded player) ──capture──▶ frames every 5 s ──▶ Clef (frame + questions) ──▶ answers.jsonl  (RECORD, cached)
                                          │                                                   │
nflverse play-by-play ──align by scorebug─┴──▶ truth labels ──────────▶ evaluate ◀────────────┤
                                                                                              ▼
                                                     events.json ──▶ replay page (YouTube embed + live panel)
```

The only new pieces vs the rocket project: **frame capture from the embedded player** (no download), **ground truth from play-by-play** instead of hand labels, a **football schema**, and **football event rules**.

---

## Repo layout

```
clef-nfl-probe/
├── README.md
├── .env.example            ← OPENROUTER_API_KEY=, BUDGET_USD=5.00, CLEF_PROBE_PATH=../clef-video-probe
├── .gitignore              ← data/frames, data/captions, runs/, .env  (frames are NFL footage: never committed)
├── requirements.txt        ← httpx pyyaml pandas python-dotenv pillow playwright
├── config.yaml             ← video id, window, interval, model, thresholds
├── schema/questions.yaml
├── docs/
│   ├── api_notes.md        ← copied from clef-video-probe
│   ├── overlay.md          ← how the story page draws Clef's answers on the video
│   ├── PROVENANCE.md       ← what was copied/changed
│   └── SOURCE.md           ← video id, window, capture method, dates
├── capture/
│   ├── embed.html          ← local page hosting the YouTube IFrame player
│   ├── capture_frames.py   ← automated capture (Playwright)
│   └── ingest_manual.py    ← fallback: ingest Win+PrtScn screenshots
├── data/
│   ├── pbp/nyj_cin_2025_w8.csv      ← committed
│   ├── frames/                       ← git-ignored
│   ├── frames_index.csv              ← frame, t_target, t_actual, flags
│   ├── video_sync.csv                ← each frame's time in the hosted Q4 video (align_video.py)
│   ├── labels/                       ← drafts, reviewed labels, events_truth.csv, grey_areas.csv
│   └── captions/                     ← git-ignored
├── src/                    ← clef_client, run_clef, align_pbp, make_labels, evaluate, detect_events, build_replay, align_video
├── runs/                   ← git-ignored
├── reports/
├── replay/                 ← timeline.json (read by the story page) and the Phase 7 flipbook
└── index.html              ← the story page: the Q4 video streamed with Clef's answers drawn on it
```

---

## The question schema

Single-choice questions. Every one has an escape option. Store verbatim in `schema/questions.yaml`; bump `version` on any change.

```yaml
version: 1
state: >
  This image is a single frame from a TV broadcast of an NFL game: the New York
  Jets (away; team colours green and white) at the Cincinnati Bengals (home; team
  colours orange and black), 26 October 2025. A scorebug along the edge of the
  screen may show team abbreviations (NYJ, CIN), each team's score, the quarter,
  the game clock, down and distance, ball position, and timeouts remaining
  (shown as small bars or dots). The main area may show live play, a replay, the
  sideline, the crowd, the broadcast booth, a graphic, or an advert. Answer only
  from what is visible in this frame.

questions:

  # ── A. Layout ───────────────────────────────────────────────────────────
  - id: main_view
    question: What does the main area of the screen show?
    options: [live_action_wide, live_action_close, replay, sideline_or_bench, crowd, coach_closeup, booth_or_studio, graphic_or_stat_card, advert, other]

  - id: scorebug_visible
    question: Is the score bar (scorebug) visible?
    options: [yes, no]

  - id: replay_showing
    question: Is this a replay (replay graphic, slow motion, or replay bug)?
    options: [yes, no, cant_tell]

  - id: player_stat_overlay
    question: Is a graphic showing an individual player's name or statistics visible?
    options: [yes, no]

  # ── B. Reading the scorebug ─────────────────────────────────────────────
  - id: quarter
    question: Which quarter does the scorebug show?
    options: ["1", "2", "3", "4", ot, not_visible]

  - id: game_clock
    question: Which range is the game clock in?
    options: ["15:00-10:00", "10:00-5:00", "5:00-2:00", "2:00-1:00", "1:00-0:00", not_visible]

  - id: jets_score
    question: What score does the scorebug show for the Jets (NYJ)?
    options: ["0", "1", ..., "59", not_visible]      # expand to the full list 0–59 in the YAML

  - id: bengals_score
    question: What score does the scorebug show for the Bengals (CIN)?
    options: ["0", "1", ..., "59", not_visible]

  - id: possession
    question: Which team has the ball according to the scorebug?
    options: [jets, bengals, cant_tell]

  - id: down
    question: Which down does the scorebug show?
    options: ["1", "2", "3", "4", none_or_not_visible]

  - id: distance
    question: How many yards to go does the scorebug show?
    options: ["1-3", "4-6", "7-10", "11+", goal, none_or_not_visible]

  - id: yards_to_goal
    question: How far is the ball from the end zone the offence is attacking?
    options: ["80+", "50-79", "20-49", "10-19", "0-9", not_visible]

  - id: jets_timeouts
    question: How many timeouts do the Jets have left?
    options: ["0", "1", "2", "3", not_visible]

  - id: bengals_timeouts
    question: How many timeouts do the Bengals have left?
    options: ["0", "1", "2", "3", not_visible]

  - id: flag_graphic
    question: Does the scorebug or screen show a penalty flag ("FLAG") indicator?
    options: [yes, no]

  - id: review_graphic
    question: Does the screen indicate a replay review or challenge is under way?
    options: [yes, no]

  # ── C. Scene ────────────────────────────────────────────────────────────
  - id: play_phase
    question: What stage of a play does this frame show?
    options: [pre_snap_lined_up, play_in_progress, after_whistle, kickoff, punt, field_goal_or_pat, kneel_down, not_a_play_shot]

  - id: referee_signal
    question: Is a referee clearly making a signal?
    options: [touchdown, first_down, incomplete, penalty, timeout, none_or_not_visible]

  - id: celebration
    question: Is a team visibly celebrating?
    options: [jets, bengals, none]

  # ── D. Editorial / money ────────────────────────────────────────────────
  - id: highlight_worthy
    question: Is this frame part of a moment worth clipping for social media (a score, big play, turnover, key reaction)?
    options: [yes, no]

  - id: ad_break_safe
    question: Would cutting away to an advert here miss nothing important?
    options: [yes, no]

  - id: sponsor_visible
    question: Is a sponsor's logo or advert clearly legible anywhere on screen?
    options: [yes, no]

  - id: sponsor_placement
    question: If a sponsor is clearly legible, where is it?
    options: [field_or_stadium_boards, broadcast_graphic, uniform_or_equipment, none]
```

**Exact scores.** Score events need exact values, so `jets_score` / `bengals_score` use integer options 0–59. If Phase 0 shows Clef rejects or degrades on 61-option questions, fall back to tens buckets plus a units-digit question and **report this at the checkpoint**.

**Groups:** A = layout (easy), B = reading the scorebug (the core skill; drives events), C = understanding the picture (hardest), D = what a client pays for.

---

## Logic

### 1. Frame timing
Frames are captured at `t_target = window_start + 5·n` (video seconds). The capture records `t_actual` (the player's real time at capture). Everything downstream uses **`t_actual`**. A frame where `|t_actual − t_target| > 1.5 s` is flagged and re-captured.

### 2. Ground truth from play-by-play (align_pbp.py)

Hand-labelling 300 frames is too slow, so truth for group B comes from the official data:

1. **Read.** Claude Code looks at each frame and drafts what the scorebug shows (quarter, clock to the second, scores, possession, down, distance, ball spot, timeouts, flag/review indicators) → `labels/scorebug_reads_draft.csv`. Unreadable → blank.
2. **Build states.** From the pbp, build the ordered list of *pre-snap states* for Q4: `(qtr, clock, posteam, down, ydstogo, yardline_100, NYJ score, CIN score, timeouts)`.
3. **Align.** Frames are in time order and states are in game order, so assign each frame to a state with a **monotone alignment** (frames never go back to an earlier state): match on scores + possession + down + distance first, clock as tie-break. Frames between plays get the state before the next snap; frames where the scorebug is hidden inherit the surrounding state but are **excluded from scoring for group B** (the truth is "not visible").
4. **Disagreements** between Claude's read and the aligned pbp state are listed in `labels/disagreements.csv` for the maintainer. The pbp wins unless the broadcast visibly shows otherwise (e.g. the 7:52 two-point review).
5. Bucket questions (`game_clock`, `distance`, `yards_to_goal`) are derived from the aligned state using the bucket edges in the schema (lower edge inclusive).

Groups A, C, D have no official data: Claude Code drafts them, **the maintainer reviews** them in `label_helper.html`. Reports must say "group A/C/D labels: AI-drafted, human-reviewed".

### 3. Scoring
As in `clef-video-probe`: accuracy (excluding `?`), macro-F1, **majority baseline beside every accuracy**, near-miss rate for bucketed numbers, **exact-match and off-by-≤1 rate for scores**, calibration, confusion matrices for `main_view` and `play_phase`, cost, latency.

### 4. Event detection (detect_events.py)

Same smoothing as the rocket project: trusted if `confidence ≥ min_conf` (0.6); a change needs `persist_frames` (2) consecutive trusted frames; `not_visible`/`cant_tell` carry no information. Football rules on the smoothed states:

| Event | Fires when |
|---|---|
| `touchdown_<team>` | that team's score rises by 6 |
| `field_goal_<team>` | score rises by 3 |
| `extra_point_good` | score rises by 1 within 4 frames after a TD |
| `two_point_good` | score rises by 2 within 4 frames after a TD |
| `conversion_no_good` | after a TD, `play_phase = kickoff` (or possession flips) with no further score change |
| `safety_<team>` | score rises by 2 with no TD before it |
| `lead_change` / `tie` | sign of (NYJ − CIN) changes / becomes 0 |
| `first_down` | `down` becomes 1 from 2/3/4, same possession |
| `possession_change` | possession flips with no score just before; sub-type `punt` if `play_phase = punt` seen, `turnover_on_downs` if previous down was 4, else `turnover` |
| `timeout_<team>` | that team's timeouts drop by 1 |
| `penalty` | `flag_graphic` turns on |
| `replay_review` | `review_graphic` turns on |
| `game_end` | `play_phase = kneel_down` and game clock in `1:00-0:00`, or clock reads 0 |

**Event truth** (`labels/events_truth.csv`): built from the pbp plays, timed at the **first frame whose aligned state reflects the play's result** (that's the earliest anyone watching the scorebug could know). The maintainer confirms the list (~15 rows). **Hit** = detected within **[−5 s, +20 s]** of truth (scorebugs update after the play, so detection is late by design). Report hits, misses, false positives, and delay per hit.

### 5. Record & replay
Cache key = SHA-256(frame bytes + schema version + state text + model id + caption text if any). Cache hit → never call the API. The replay page reads files only.

---

## Phases

Each phase: **Goal → Tasks → Outputs → Checkpoint.** At the checkpoint, print the report and stop.

### Phase 0 — Bootstrap from clef-video-probe

**Goal:** a working repo reusing the existing pipeline, and confirmation the video can be embedded.

**Tasks**
1. Read `../clef-video-probe/README.md`, its `docs/api_notes.md` and `src/`. Copy the files in [What carries over](#what-carries-over-from-clef-video-probe); write `docs/PROVENANCE.md`.
2. Create the layout, `config.yaml`, `.gitignore`, `.env.example`, and `schema/questions.yaml` (expand score options to the full 0–59 lists).
3. Put the play-by-play at `data/pbp/nyj_cin_2025_w8.csv` (the maintainer provides it). Print the Q4 plays and check they match [the table above](#why-the-4th-quarter).
4. Write `capture/embed.html` (see Phase 1) and serve it with `python -m http.server`. Load video `DG5G2uif0DU` and check the IFrame API's `onError`: codes **101/150 mean embedding is disabled**. If so, stop and report — the capture and replay plans both change.

**Checkpoint report:** files copied and changes made; schema written; pbp check result; **embeddable: yes/no**.

---

### Phase 1 — Locate the window and pilot capture

**Goal:** know exactly where Q4 sits in the video, and prove the capture works on 10 frames.

**Capture method (automated):** `capture/capture_frames.py` uses **Playwright** with the locally installed Chrome (`channel="chrome"`, headed, so no browser download and normal codecs), opens `http://localhost:8000/capture/embed.html`, and for each target time:

- `embed.html` hosts the IFrame player at **1920×1080** with `controls=0, disablekb=1, rel=0, iv_load_policy=3, cc_load_policy=0, playsinline=1`, **muted**, and exposes a JS function `seekTo(t)` that seeks, lets the video **play** (a paused embed shows a "More videos" overlay, so don't pause), waits until state is `PLAYING` and the frame has settled (~1 s), then returns `getCurrentTime()`.
- Python then takes an **element screenshot of the player** → `data/frames/f_0000.png`, and logs `frame, t_target, t_actual` to `data/frames_index.csv`.
- One seek every ~3 s, a single tab, no parallelism, no downloading of the video stream.

**Tasks**
1. **Find Q4.** If the maintainer has put `q4_start_video_s` / `q4_end_video_s` in `config.yaml`, use them. Otherwise do a coarse pass: one frame every 60 s, read the scorebug quarter and clock, narrow to ±5 s around the Q4 opening snap and the final kneel-down. Write the window to `config.yaml` and `docs/SOURCE.md`.
2. **Pilot:** capture 10 frames at 5 s intervals starting at the window start.
3. Look at each pilot frame and check: scorebug readable, no player controls/overlays/captions, no ads, `t_actual` within 1.5 s of target, image is 1920×1080.
4. Run **one Clef call** on 3 pilot frames with the full schema (this is the only API use before Phase 4). Show answers next to what the frames actually show. Confirm the 61-option score questions are accepted.

**Fallback (manual capture):** if automated capture fails (bot checks, ads every seek, low resolution, blocked), say so and switch to `capture/ingest_manual.py`: the maintainer plays the video fullscreen at 1080p, captions off, cursor hidden, and repeats **→ (jump 5 s) → wait 2 s → Win+PrtScn**. The script takes the Windows `Pictures\Screenshots` files in numeric order, assigns `t_target = window_start + 5·n`, copies them to `data/frames/`, and asks the maintainer to confirm the first frame's time.

**Checkpoint report:** the Q4 window (video start/end, duration, expected frame count); the 10 pilot frames as a contact sheet; per-frame QC table; the 3-frame Clef result with tokens and cost per frame; **recommendation: automated or manual capture.**

> *Maintainer checks:* frames look clean? Q4 window right? Projected cost acceptable?

---

### Phase 2 — Full capture

**Goal:** every frame of Q4, clean and in order.

**Tasks**
1. Capture the full window at 5 s (resume-safe: skip frames already captured).
2. QC pass: re-capture frames flagged for timing; detect **adverts or non-game frames** (no scorebug + not booth/crowd/replay — inspect visually) and **near-duplicates**; list them in `frames_index.csv` `flags`. Never silently drop frames.
3. Contact sheet of the whole quarter (one tile per 30 s, labelled with `t_actual`) → `reports/q4_contact_sheet.jpg`.

**Checkpoint report:** frame count, flags by type, contact sheet, total capture time.

---

### Phase 3 — Ground truth

**Goal:** truth labels for every frame, mostly from official data.

**Tasks**
1. Draft scorebug reads for every frame (Claude Code reading the images) → `labels/scorebug_reads_draft.csv`.
2. `align_pbp.py`: align frames to pbp states as in [Logic §2](#2-ground-truth-from-play-by-play-align_pbppy) → `labels/labels_B.csv` (group B truth), `labels/disagreements.csv`.
3. Draft groups A, C, D for every frame → `labels/labels_ACD_draft.csv`. Update `label_helper.html` to show each frame with its draft labels so the maintainer can correct them quickly (corrections saved as `labels_ACD_reviewed.csv`; the helper can export a CSV the maintainer saves).
4. Draft `labels/events_truth.csv` from the pbp + alignment, one row per event with `t_video`, `game_clock`, `pbp play_id`, description.

**Checkpoint report:** alignment coverage (% of frames aligned, % with hidden scorebug), number of disagreements with examples, the draft event list, and how to open the helper. **Then wait:** the maintainer reviews A/C/D labels and confirms events (including the 7:52 two-point call) before "go".

---

### Phase 4 — Run Clef, image only (RECORD)

**Goal:** Clef's answers for every frame, from pixels alone.

Reuse `run_clef.py`: **dry run first** (projected cost from Phase 1's per-frame tokens; refuse if above `BUDGET_USD`), then the real run, cache-first, retries with backoff, raw responses saved, `answers.jsonl` + `run_meta.json`, validation that every frame answered every question.

**Checkpoint report:** run id, frames, cache hits, failures, cost, median/p95 latency, and a 5-frame spot check (answers vs truth side by side).

---

### Phase 5 — Evaluate

As in `clef-video-probe` Phase 4, plus: exact-match and off-by-≤1 for scores; per-team accuracy for timeouts and celebration; accuracy split by `main_view` (does it read the scorebug as well during replays as during live play?). Write `reports/accuracy.md` + charts.

**Checkpoint report:** summary table with baselines, 3-line verdict, the 10 worst frames.

---

### Phase 6 — Event detection

As in `clef-video-probe` Phase 5 with the [football rules](#4-event-detection-detect_eventspy). Sensitivity grid `min_conf ∈ {0.5, 0.6, 0.7}` × `persist_frames ∈ {1, 2, 3}` (no new API calls). Highlight in the report whether it caught **the lead change at 1:54** and **both two-point attempts**.

**Checkpoint report:** detected vs true events with delays, sensitivity table, recommended settings.

---

### Phase 7 — Replay page (the demo)

**Goal:** the client-facing demo, with the video played through YouTube's own player.

1. `replay/index.html` embeds the YouTube IFrame player (video id + window start from config). Poll `getCurrentTime()` every 250 ms; show the latest frame's answers with `t_actual ≤ currentTime`.
2. Panel: **Now** (scorebug as Clef read it — score, quarter, clock bucket, possession, down & distance, field position — plus scene and confidence bars), **Events** log that grows as play passes them (click → `seekTo`), **Moments** strip (`highlight_worthy`), **Sponsor exposure** running total (seconds with `sponsor_visible = yes`, by placement), **Show ground truth** toggle (mismatches in red), footer (model, schema version, run id, cost, "internal — not for distribution").
3. Timeline under the video with event markers.
4. If embedding is disabled (Phase 0), fall back to a **frame flipbook**: show captured frames in sequence at the same timing, with an "open on YouTube at this moment" link.

**Checkpoint report:** how to open it, a screenshot, known sync issues.

---

### Phase 8 — Captions comparison (Run B)

**Goal:** measure how much the commentary adds on top of pixels.

1. **Get the transcript.** Try first: in the browser, open the video page → "…more" → **Show transcript**, copy the panel's text (timestamps + lines) into `data/captions/transcript_raw.txt`. If that doesn't work, ask the maintainer to paste it manually.
2. Parse to `(t_start, text)` rows. For each frame, the caption context is all lines with `t_start ∈ [t_actual − 10 s, t_actual]`.
3. `run_clef.py --with-captions`: same frames and schema, `state` gets an extra paragraph: *"Commentary from the last 10 seconds: …"*. Separate run id, separate cache keys.
4. Compare Run A vs Run B per question group and for events. Note in the report: these are captions on an uploaded video, so they are synced; real live captions lag by a few seconds.

**Checkpoint report:** A vs B table, which questions improved, cost difference.

---

### Phase 9 — Pitch summary

`reports/summary.md`, one page: what was tested, headline numbers (scorebug reading accuracy, event hit rate, delay, cost per broadcast hour), image-only vs image+captions, three strengths, three limitations, and the [business angle](#business-angle) applied to the actual results. Also list **the remaining changes needed on the `clef-video-probe` GitHub page** if the maintainer has supplied them.

---

## Rules for Claude Code

1. **Phases in order, stop at every checkpoint**, wait for "go".
2. **Reuse `clef-video-probe` code**; don't rewrite what works. Record what changed.
3. **Never guess the Clef API** — use `docs/api_notes.md`; if anything new is needed, read the docs and append.
4. **Spend tokens only through `run_clef.py`**: dry run first, cache-first, never above `BUDGET_USD`. The Phase 1 three-frame check is the only exception.
5. **Capture gently:** one tab, one seek every ~3 s, no parallel sessions, **never download the video or its stream**, never bypass ads, logins or bot checks — if one appears, stop and report (that means switching to manual capture).
6. **Never commit** frames, captions, `runs/`, or `.env`. This repo stays **private**.
7. **Labels:** the pbp is the source of truth for group B; AI-drafted A/C/D labels must be marked as drafts until the maintainer reviews them. Never edit reviewed labels yourself — list suspected errors at the checkpoint.
8. **Report honestly:** baselines beside accuracy, worst frames included, failures shown.
9. **Keep it simple:** small scripts, no frameworks, no databases.

---

## Usage & compliance

- **Internal research only.** The video is the NFL's copyrighted broadcast. Nothing from it (frames, captions, clips) is committed or redistributed; the repo is private.
- **The demo plays the video through YouTube's embedded player**, so the client watches it from the official source.
- **Automated capture of YouTube content is restricted by YouTube's Terms of Service.** It is used here only with the manager's explicit sign-off; the manual fallback (a person taking screenshots while watching) exists for exactly this reason. If in doubt, use manual capture.
- **nflverse play-by-play** is used for ground truth; credit nflverse in any report.

---

## Business angle

Pitched in the client's terms:

| Use | What Clef provides |
|---|---|
| **Auto-clipping for social** | every TD, conversion, lead change and big play flagged within seconds, with the video timestamp — clips can be cut and posted without someone watching the feed |
| **Sponsor exposure measurement** | seconds each frame shows a legible sponsor, split by placement (stadium boards / broadcast graphics / uniforms) — a number partnership teams sell against |
| **Live second-screen / app updates** | scorebug state and events in near real time, straight from the broadcast picture |
| **Archive tagging** | search a season of footage for "every Jets red-zone play" or "every replay review" |

Clef's fit: typed answers (no text parsing), a probability with every answer, free output tokens, and a smaller **Clef-Flash** for live use (optional comparison, same as the rocket project).

---

## Credits

- Video: *New York Jets vs. Cincinnati Bengals, 2025 Week 8* — official NFL YouTube upload (https://youtu.be/DG5G2uif0DU). © NFL. Internal use only.
- Play-by-play: **nflverse** (https://github.com/nflverse).
- Model: Cloudflare Clef / Clef-Flash.
- Pipeline: adapted from `clef-video-probe`.