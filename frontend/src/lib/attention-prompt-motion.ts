import {createGallerySpring, gallerySpatial} from './gallery-spring';

/** Direct manipulation and settling share the gallery's continuous spring. */
export function bindAttentionPromptMotion(card: HTMLElement, signal: AbortSignal, acknowledge: () => void) {
  const mobile = matchMedia('(max-width:650px)');
  const reduced = matchMedia('(prefers-reduced-motion:reduce)');
  let wanted = !card.hidden;
  let ignoreClickUntil = 0;
  let gesture: {id: number; x: number; y: number; axis?: 'x' | 'y'; distance: number;
    samples: {x: number; time: number}[]} | undefined;
  const motion = createGallerySpring({x: 0, y: 0, alpha: 1}, value => {
    card.style.translate = `${value.x}px ${value.y}px`;
    card.style.opacity = String(value.alpha);
  }, signal, {stiffness: gallerySpatial.default, bounds: {alpha: {min: 0, max: 1}}});

  const releaseCapture = () => {
    const id = gesture?.id;
    gesture = undefined;
    delete card.dataset.dragging;
    if (id !== undefined && card.hasPointerCapture?.(id)) card.releasePointerCapture(id);
  };
  const home = () => {
    releaseCapture();
    if (wanted) motion.to({x: 0, y: 0, alpha: 1}, {gesture: true, immediate: reduced.matches});
  };
  const hide = (direction = 0) => {
    if (card.hidden || !wanted) return;
    wanted = false;
    releaseCapture();
    card.inert = true;
    motion.to({x: direction * (window.innerWidth + card.getBoundingClientRect().width), y: direction ? 0 : 12, alpha: 0}, {
      gesture: !!direction, immediate: reduced.matches, done: () => {
        if (wanted) return;
        card.hidden = true;
        card.style.removeProperty('translate');
        card.style.removeProperty('opacity');
      },
    });
  };
  const dismiss = (direction = 0) => {
    if (card.hidden || !wanted) return;
    acknowledge();
    hide(direction);
  };
  const show = () => {
    if (wanted && !card.hidden) return;
    wanted = true;
    releaseCapture();
    if (card.hidden) motion.snap({x: 0, y: 20, alpha: 0});
    card.hidden = false;
    card.inert = false;
    motion.to({x: 0, y: 0, alpha: 1}, {immediate: reduced.matches});
  };

  card.addEventListener('pointerdown', event => {
    if (!mobile.matches || !wanted || card.hidden || event.button !== 0) return;
    if (!event.isPrimary) { home(); return; }
    const x = motion.stop().value.x;
    gesture = {id: event.pointerId, x: event.clientX - x, y: event.clientY, distance: x,
      samples: [{x: event.clientX, time: performance.now()}]};
  }, {signal, passive: true});
  card.addEventListener('pointermove', event => {
    if (!gesture || event.pointerId !== gesture.id) return;
    const dx = event.clientX - gesture.x, dy = event.clientY - gesture.y;
    if (!gesture.axis) {
      if (Math.max(Math.abs(dx), Math.abs(dy)) < 8) return;
      gesture.axis = Math.abs(dx) > Math.abs(dy) * 1.2 ? 'x' : 'y';
    }
    if (gesture.axis !== 'x') return;
    event.preventDefault();
    if (!card.hasPointerCapture(event.pointerId)) card.setPointerCapture(event.pointerId);
    const now = performance.now();
    gesture.samples.push({x: event.clientX, time: now});
    while (gesture.samples.length > 2 && gesture.samples[0].time < now - 100) gesture.samples.shift();
    gesture.distance = dx;
    card.dataset.dragging = '';
    motion.drag({x: dx, y: 0, alpha: 1 - .6 * Math.min(1, Math.abs(dx) / card.getBoundingClientRect().width)});
  }, {signal, passive: false});
  const release = (event: PointerEvent) => {
    if (!gesture || event.pointerId !== gesture.id) return;
    if (gesture.axis !== 'x') { home(); return; }
    ignoreClickUntil = performance.now() + 400;
    const first = gesture.samples[0], last = gesture.samples.at(-1)!;
    const elapsed = performance.now() - first.time;
    const velocity = elapsed > 0 && performance.now() - last.time <= 100 ? (last.x - first.x) / elapsed : 0;
    const distance = gesture.distance;
    const direction = Math.sign(distance);
    const threshold = Math.min(120, Math.max(72, card.getBoundingClientRect().width * .35));
    const flick = Math.abs(distance) >= 32 && velocity * direction >= .5;
    if (event.type === 'pointerup' && velocity * direction >= -.2 && (Math.abs(distance) >= threshold || flick)) dismiss(direction);
    else home();
  };
  window.addEventListener('pointerup', release, {signal, passive: true});
  window.addEventListener('pointercancel', release, {signal, passive: true});
  card.addEventListener('lostpointercapture', event => {
    // Touch starts with implicit capture on the touched child. Its loss bubbles
    // when the card takes over; only losing the card's own capture ends a drag.
    if (event.target !== card || !gesture || event.pointerId !== gesture.id || card.hasPointerCapture(event.pointerId)) return;
    home();
  }, {signal});
  card.addEventListener('click', event => {
    if (performance.now() < ignoreClickUntil) { event.preventDefault(); event.stopImmediatePropagation(); }
  }, {signal, capture: true});
  card.addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); dismiss(); }
  }, {signal});
  window.addEventListener('blur', () => { if (gesture) home(); }, {signal});
  window.addEventListener('pagehide', () => { if (gesture) home(); }, {signal});
  mobile.addEventListener('change', home, {signal});
  reduced.addEventListener('change', () => { if (reduced.matches) motion.finish(); }, {signal});
  signal.addEventListener('abort', () => {
    releaseCapture();
    card.style.removeProperty('translate');
    card.style.removeProperty('opacity');
  }, {once: true});
  return {show, hide, dismiss};
}
