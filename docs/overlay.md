# Drawing a model's answers on a broadcast clip

These notes cover how the overlay on the clef-video-probe story page (`index.html`) works, and why it is built that way. They are written so the same design can be reused on another clip, such as an NFL broadcast. The question list will differ there. The layout, the rules and the mechanics carry over.

## The goal

Showing someone a table of questions and answers creates friction. The overlay draws the model's answers on the clip, pointing at the thing each answer is about. A viewer can see whether the model was right without reading a question. The overlay was suggested by a manager reviewing the project, and it is the main way the project is pitched.

## The setup

- The clip is our own MP4, played by a plain `<video>` element. Never use a YouTube embed: its terms do not allow drawing over the player, and you cannot draw over a cross-origin iframe anyway.
- The model (Cloudflare Clef) samples one frame every 5 s. On each frame it answers multiple-choice questions and gives a confidence for each answer. Every answer is scored against a hand label.
- The overlay reads one JSON file. The page never calls the model; it is a replay.

The data each frame needs:
```
{"meta": {...}, "frames": [{"t": 0.0, "frame": "f_0000_t000.0.jpg",
  "answers": {"<question_id>": {"a": "<option>", "c": 0.94}, ...},
  "truth":   {"<question_id>": "<option>", ...}}]}
```
- `truth` may leave a question out. A missing truth means the answer is not scored, which is different from right or wrong.
- At playback time `t`, show the last frame with `frame.t <= t`. Each answer holds until the next sample.

## Design decisions

These came from the user, who owns the project. Reuse them unless they say otherwise.

### Only things on the picture go on the picture

- **On the video:** only overlays that point at something visible and fixed, such as a box around a graphic, a tick next to a printed number, or a short tag about the whole shot.
- **Beside the video:** answers about what is happening physically. The model gives no coordinates, so it cannot point at those things. Drawing them over the action hid what the viewer was trying to watch. In the rocket clip these were booster, fairing, engine and flight-phase answers. In the NFL clip they would be things like "pass or run", "turnover" or "player down".
- **In a strip under the video:** editorial or judgement answers, such as "highlight moment?" or "safe to cut to an ad?". These are kept but stay off the picture.

### What appears on screen, and when

The rule is to avoid clutter. Do not show every answer.

- **The main shot type** (what the main picture shows) is always shown, in a small tag in the top-left corner.
- **Yes/no "is X visible" questions:** draw an outline around X while the model says it is there. Draw a dashed red outline if X is there and the model missed it. Otherwise draw nothing.
- **Rare yes/no answers** (such as "people on screen"): show the tag when the answer turns yes, then fade it out after about 10 s. A wrong answer stays on screen.
- **An on-screen clock:** box it while it is visible. When it disappears, show "Clock not visible ✓" for about 10 s, then nothing.
- **Printed numbers:** use one combined tag ("Clef reads all three ✓") and a small ✓ badge beside each figure. On a miss, put a red box around that figure and change the tag to red with the detail ("Distance: Clef says X, it is Y, 53%"). The user confirmed the model reads printed numbers well, so a red mark there should draw attention. Answers given as ranges ("100–500 km") are acceptable.
- **The answers beside the video** (the vehicle card in the rocket clip) dim while an answer is right and unchanged. An answer is fully visible for 10 s after it changes. Wrong answers stay fully visible, in red, with "should be X" on its own line.
- **Grey areas** are shown in amber with an asterisk, not in red. These are frames where the label is debatable. The explanation goes in the strip under the video, not on the picture.
- **A replay note** sits in the strip. It says the model sees one frame every N s, answers in about 1.5 s, and that each answer holds until the next frame.
- **A message line** sits in the strip, with one sentence on what is happening in this stretch of the clip. These are written by hand for about 10 key moments, and they are what makes the overlay work in a pitch. Include the misses; a demo that shows its failures convinces people more than a perfect one.

The colours:
- ✓ green: right.
- ✗ red: wrong.
- Grey, no mark: not scored.
- Amber confidence %: the model was under 60% sure.
- Amber with an asterisk: a grey area.

### Fullscreen is required

The demo is presented on a laptop or desktop, in fullscreen. iPhone support is not needed.

- The video is pinned to the top-left. The answers beside the video sit in a column on the right (about 330 px). The strip runs full width under both.
- In the normal page view the answers beside the video sit directly under the clip, in two columns.

## Mechanics

**1. Fullscreen the container, not the video.**
- The browser's own fullscreen (from the video's controls or a double-click) takes only the `<video>`, and the overlay disappears.
- So: wrap the video, the overlay, the side card and the strip in one `#stage`.
- Drop the native `controls` attribute and build a control row: play, time, seek range, mute, overlay toggle, fullscreen.
- Call `stage.requestFullscreen()` (with a `webkitRequestFullscreen` fallback) from the button, from a double-click on the video, and from the F key.
- Other keys: O toggles the overlay, I hides the strip, space or K plays. Arrow keys seek ±5 s, in fullscreen only.

**2. Write positions in the clip's own pixels.**
- Measure each fixed graphic once on a full-resolution frame. Store it as `x, y, w, h` in source pixels (here 1080×606).
- Emit each one as `left/top/width/height` percentages of the source size.
- The overlay is `position:absolute; inset:0` over a box with the same aspect ratio as the video, so it stays aligned at any size.

**3. Scale text with the video.**
- Make the video box a size container (`container-type: inline-size`).
- Set the overlay font to `clamp(9px, 1.2cqw, 21px)` and size everything else in `em`.

**4. Fit the video in fullscreen without stretching it.**
- The area holding the video becomes `container-type: size` in fullscreen.
- The video box width is `min(100cqw - <side column>, 100cqh * W / H)`.

**5. Build the overlay elements once.**
- Then on each new frame, only toggle classes (`gone`, `dim`, `dash`) and set `--c`.
- Rebuilding `innerHTML` on every frame would kill the CSS opacity transitions that make the fades work.

**6. Let clicks through.** The overlay has `pointer-events:none`, so a click on the video plays or pauses it.

**7. Use one answer helper.** It returns, for a question on frame `i`:
- the answer and its confidence;
- `ok` (true, false, or null when not scored);
- `fresh`: whether the answer changed on this frame or the previous one, which is 10 s at 5 s sampling.

All the show, fade and red rules above are built from this.

**8. Serving locally.**
- Python's `http.server` does not serve byte ranges, so seeking breaks.
- On localhost, fetch the MP4 as a blob and use an object URL.

**9. Testing.**
- Use Playwright with `channel="chrome"` against a local server.
- Wait for the data and `readyState >= 2`.
- Seek to the key moments and screenshot `#stage`.
- Click the fullscreen button and screenshot at 1440×900 (16:10) and 1920×1080 (16:9).
- Check that `document.fullscreenElement.id === 'stage'` and collect page errors.

## What will differ for NFL

- **The score bug is not static across a game.** Its position and design change by network, in replays, during ad breaks and after transitions.
  - Measure positions per broadcaster.
  - Make the overlay depend on the shot type. Draw the score-bug boxes only when the model says the score bug is visible, and when the main shot is a live game view. A replay or a graphic may use a different layout.
  - Sample several frames to find each layout.
- **Printed graphics are the strength; reading the play is the weak spot.**
  - Down and distance, the clock, the score and the timeouts are printed graphics, the equivalent of the telemetry numbers here. Expect the model to do well on them, and point at them on the picture.
  - Pass, run, tackle and turnover are the equivalent of the rocket's boosters. Expect weakness there, and keep them beside the picture.
- **The model never gives locations.** Do not draw arrows at players or the ball unless the positions come from somewhere else, such as hand marking. Leave that as a later, separate step.
- **Plays are short.** A 5 s sample can miss a whole snap-to-whistle. Consider 1–2 s sampling around plays. The "fresh" window should then be counted in seconds, not in frames.
- **Footage rights.** NFL footage is tightly licensed. Use it internally only unless rights are cleared.
