// Native dialog focus restoration must not reopen pointer/touch poster controls.
// Keep focus available for keyboard users; a fresh interaction reveals them again.
export function dismissPosterControls(opener: HTMLElement | null, signal: AbortSignal) {
  const card = opener?.closest<HTMLElement>('[data-browse-card]');
  if (!card) return;
  card.classList.remove('is-touch-active');
  card.dataset.controlsDismissed = '';
  const listeners = new AbortController();
  const reset = () => {
    delete card.dataset.controlsDismissed;
    listeners.abort();
    signal.removeEventListener('abort', reset);
  };
  signal.addEventListener('abort', reset, {once:true});
  document.addEventListener('pointermove', event => {
    if (event.pointerType === 'mouse' && (event.movementX || event.movementY)) reset();
  }, {signal:listeners.signal});
  document.addEventListener('pointerdown', reset, {signal:listeners.signal});
  document.addEventListener('keydown', reset, {signal:listeners.signal});
}
