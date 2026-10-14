// Page-side half of the capture. Runs on the youtube.com watch page in the maintainer's own
// signed-in browser (pasted into the console, or run through the Claude in Chrome extension);
// capture/receive_frames.py must be running. One seek at a time.
//
//   grabFrames({start: 5095, count: 408})                           // full-size PNGs to data/frames
//   grabFrames({start: 5095, count: 408, redo: ['f_0011.png']})     // also re-grab these
//   grabFrames({start: 4800, count: 40, interval: 60, type: 'image/jpeg'})   // coarse pass
//
// The player is kept PAUSED and seeked frame by frame, so t_actual is the seek target itself.
// (Grabbing while playing drifted by up to 12 s when the tab was throttled.) The frame is drawn
// from the <video> element, so the paused player's overlays are not in it.
// Progress is in window.__grab; set window.__grab.stopped = 'reason' to stop it. It stops by
// itself if an advert or another video shows up.
window.grabFrames = async function ({start, count, interval = 5, first = 0, type = 'image/png', redo = [],
                                     server = 'http://127.0.0.1:8011', gapMs = 2500, maxDrift = 1.5}) {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const p = document.getElementById('movie_player'), v = document.querySelector('video.html5-main-video');
  const videoId = p.getVideoData().video_id, duration = p.getDuration();
  const G = window.__grab = {done: 0, total: count, last: null, flagged: [], stopped: null, running: true};
  const have = new Set((await (await fetch(server + '/')).text()).split(',').filter(Boolean));
  redo.forEach(n => have.delete(n));
  p.mute();
  if (p.setPlaybackQualityRange) p.setPlaybackQualityRange('hd1080', 'hd1080');
  const canvas = document.createElement('canvas');

  async function seek(t) {
    p.pauseVideo();
    p.seekTo(t, true);
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
      if (!v.seeking && v.readyState >= 2 && Math.abs(v.currentTime - t) < 0.25) break;
      await sleep(100);
    }
    await new Promise(r => { if (v.requestVideoFrameCallback) v.requestVideoFrameCallback(() => r()); setTimeout(r, 700); });
  }

  for (let n = first; n < first + count && !G.stopped; n++) {
    const name = `f_${String(n).padStart(4, '0')}.${type === 'image/png' ? 'png' : 'jpg'}`;
    if (have.has(name)) { G.done++; continue; }
    const tTarget = start + interval * n, tick = Date.now();
    let flags = [], tActual = 0;
    for (let attempt = 0; attempt < 3; attempt++) {
      await seek(tTarget);
      if (p.classList.contains('ad-showing') || p.getVideoData().video_id !== videoId || Math.abs(p.getDuration() - duration) > 1) {
        G.stopped = `${name}: an advert or another video is showing; stopped`;
        break;
      }
      canvas.width = v.videoWidth; canvas.height = v.videoHeight;
      canvas.getContext('2d').drawImage(v, 0, 0);
      tActual = v.currentTime;
      flags = Math.abs(tActual - tTarget) > maxDrift ? ['timing'] : [];
      if (v.videoHeight < 1080) flags.push(`size_${v.videoWidth}x${v.videoHeight}`);
      if (!flags.length) break;
    }
    if (G.stopped) break;
    const blob = await new Promise(r => canvas.toBlob(r, type, 0.9));
    const q = new URLSearchParams({name, t_target: tTarget.toFixed(1), t_actual: tActual.toFixed(2), flags: flags.join(' ')});
    const res = await fetch(`${server}/frame?${q}`, {method: 'POST', body: blob});
    if (!res.ok) { G.stopped = `${name}: receiver answered ${res.status}`; break; }
    if (flags.length) G.flagged.push(name);
    G.done++;
    G.last = {name, tTarget, tActual};
    await sleep(Math.max(0, gapMs - (Date.now() - tick)));
  }
  G.running = false;
  return G;
};
