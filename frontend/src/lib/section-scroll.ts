import { createGallerySpring, gallerySpatial } from './gallery-spring';

/** Quick M3 spatial motion, critically damped so a reading destination never bounces. */
export function createSectionScroll(signal: AbortSignal) {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const limit = () => Math.max(0, document.documentElement.scrollHeight - innerHeight);
  const motion = createGallerySpring({y:scrollY}, ({y}) => {
    window.scrollTo({top:y, behavior:'instant'});
  }, signal, {
    stiffness:gallerySpatial.fast,
    precision:{y:.5},
    bounds:() => ({y:{min:0,max:limit()}}),
  });
  const interrupt = () => { if (motion.active) motion.stop(); };
  for (const event of ['wheel','touchstart','pointerdown']) {
    window.addEventListener(event, interrupt, {signal,passive:true});
  }
  window.addEventListener('keydown', event => {
    if (['ArrowUp','ArrowDown','PageUp','PageDown','Home','End',' '].includes(event.key)) interrupt();
  }, {signal});
  reduced.addEventListener('change', () => { if (reduced.matches && motion.active) motion.finish(); }, {signal});
  return (target: HTMLElement) => {
    if (signal.aborted) return;
    // Respect the section's sticky-navigation clearance and the document boundary.
    const margin = parseFloat(getComputedStyle(target).scrollMarginTop) || 0;
    const top = Math.max(0, Math.min(limit(), scrollY + target.getBoundingClientRect().top - margin));
    if (!motion.active) motion.snap({y:scrollY});
    motion.to({y:top}, {immediate:reduced.matches});
  };
}
