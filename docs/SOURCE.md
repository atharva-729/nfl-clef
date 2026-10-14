# Source video

| | |
|---|---|
| Title | Game of the Week! New York Jets vs. Cincinnati Bengals FULL GAME \| NFL 2025 Season Week 8 |
| Publisher | NFL (official YouTube channel) |
| URL | https://youtu.be/DG5G2uif0DU |
| Video id | `DG5G2uif0DU` |
| Game | New York Jets at Cincinnati Bengals, 26 Oct 2025, final NYJ 39 – CIN 38 |
| nflverse `game_id` | `2025_08_NYJ_CIN` |

© NFL. Internal research only. No frame, caption or clip from this video is committed or
redistributed; `data/frames*/` and `data/captions/` are git-ignored.

## Window

The whole 4th quarter, from the Q4 opening snap to the final kneel-down.

| | Video time |
|---|---|
| Q4 start (`video.q4_start_video_s`) | **5095 s** (1:24:55). The scorebug first reads 4th / 15:00 at about 5096 s; Q3 ends at about 5071 s |
| End (`video.q4_end_video_s`) | **7130 s** (1:58:50). Kneel-down at about 7096 s, FINAL graphic at about 7126 s; the video ends at 7132.6 s |
| Duration / frames at 5 s | 2035 s, 408 frames |

Found on 2026-10-12 with a coarse pass (one frame a minute from 4800 s) and two 5 s passes around
each end, reading the scorebug.

## Capture method

Captured 2026-10-12, automated, in the maintainer's own signed-in browser on the normal
youtube.com watch page, at the maintainer's instruction.

- `capture/grab_in_page.js` runs in the page: seek, wait until playing, draw the `<video>` element
  to a canvas at its native 1920x1080, POST the PNG to `capture/receive_frames.py` on localhost,
  which writes `data/frames/f_NNNN.png` and `data/frames_index.csv`.
- One tab, one seek at a time, about one frame every 5 s of wall time. Only still frames are
  taken; the stream is not downloaded. The frame comes from the video element itself, so there
  are no player controls, overlays or captions in it.
- `t_actual` is the video element's `currentTime` at the moment of the grab (typically
  `t_target` + 0.5 s, because the player is left playing while the frame settles).

What did not work, and why this method was used instead of the README's:

- **Embedded player:** the IFrame API returns `onError` 150 (the owner has disabled embedding),
  in Chrome and in Brave. A known-embeddable control video played normally on the same page.
- **Playwright on the watch page (fresh profile, Brave and Chrome):** the video plays from 0:00
  but every seek hangs in buffering, with 403s on the stream requests.
- `capture/embed.html` and `capture/capture_frames.py` are kept for a video that can be embedded;
  `capture/ingest_manual.py` remains the manual fallback.

Because embedding is disabled, the replay page (Phase 7) needs the frame-flipbook fallback.

## Ground truth

Play-by-play: nflverse (https://github.com/nflverse), `data/pbp/nyj_cin_2025_w8.csv`.
