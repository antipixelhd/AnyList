export function carousel(track: HTMLElement, reduced: MediaQueryList) {
  const events = new AbortController(), card = track.closest<HTMLElement>('.stats-genre-card')!;
  const previous = card.querySelector<HTMLButtonElement>('[data-genre-scroll="-1"],[data-actor-scroll="-1"],[data-studio-scroll="-1"]')!;
  const next = card.querySelector<HTMLButtonElement>('[data-genre-scroll="1"],[data-actor-scroll="1"],[data-studio-scroll="1"]')!;
  let frame = 0, pointer: number | null = null, startX = 0, startScroll = 0, dragging = false, suppressClickUntil = 0;
  const bounds = () => { previous.hidden = track.scrollLeft <= 1; next.hidden = track.scrollLeft >= track.scrollWidth - track.clientWidth - 1; };
  const cancel = () => { cancelAnimationFrame(frame); frame = 0; };
  const resize = new ResizeObserver(bounds); resize.observe(track);
  track.addEventListener('scroll', bounds, { passive: true, signal: events.signal });
  track.addEventListener('dragstart', event => event.preventDefault(), { signal: events.signal });
  track.addEventListener('pointerdown', event => {
    cancel();
    if (event.pointerType !== 'mouse' || event.button !== 0) return;
    pointer = event.pointerId; startX = event.clientX; startScroll = track.scrollLeft; dragging = false;
  }, { signal: events.signal });
  track.addEventListener('pointermove', event => {
    if (pointer !== event.pointerId) return;
    const delta = event.clientX - startX;
    if (!dragging && Math.abs(delta) < 6) return;
    if (!dragging) { dragging = true; track.setPointerCapture(event.pointerId); track.classList.add('is-dragging'); }
    event.preventDefault(); track.scrollLeft = startScroll - delta;
  }, { signal: events.signal });
  const finish = () => {
    if (dragging) suppressClickUntil = performance.now() + 500;
    const captured = pointer;
    pointer = null; dragging = false;
    if (captured !== null && track.hasPointerCapture(captured)) track.releasePointerCapture(captured);
    track.classList.remove('is-dragging'); bounds();
  };
  track.addEventListener('pointerup', finish, { signal: events.signal });
  track.addEventListener('pointercancel', finish, { signal: events.signal });
  track.addEventListener('lostpointercapture', finish, { signal: events.signal });
  track.addEventListener('pointerleave', () => { if (!dragging) pointer = null; }, { signal: events.signal });
  track.addEventListener('click', event => {
    if (performance.now() < suppressClickUntil) { event.preventDefault(); event.stopPropagation(); }
  }, { capture: true, signal: events.signal });
  function scroll(direction: number) {
    cancel();
    const start = track.scrollLeft, end = Math.max(0, Math.min(track.scrollWidth - track.clientWidth, start + direction * (track.clientWidth + 10)));
    if (reduced.matches) { track.scrollLeft = end; bounds(); return; }
    const began = performance.now();
    const step = (now: number) => {
      const t = Math.min((now - began) / 500, 1), ease = t < .5 ? 4 * t ** 3 : 1 - (-2 * t + 2) ** 3 / 2;
      track.scrollLeft = start + (end - start) * ease; bounds();
      if (t < 1) frame = requestAnimationFrame(step); else frame = 0;
    };
    frame = requestAnimationFrame(step);
  }
  track.addEventListener('keydown', event => {
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); scroll(event.key === 'ArrowLeft' ? -1 : 1); }
  }, { signal: events.signal });
  track.addEventListener('wheel', cancel, { passive: true, signal: events.signal });
  const onMotionChange = () => { cancel(); bounds(); }; reduced.addEventListener('change', onMotionChange, { signal: events.signal });
  bounds();
  return { scroll, bounds, stop() { cancel(); finish(); resize.disconnect(); events.abort(); } };
}

