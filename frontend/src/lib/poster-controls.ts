// Native dialog focus restoration must not reopen pointer/touch poster controls.
// Keep focus available for keyboard users; a fresh interaction reveals them again.
export function dismissPosterControls(opener: HTMLElement | null, signal: AbortSignal) {
  const card = opener?.closest<HTMLElement>('[data-browse-card],.entry-row');
  if (!card) return;
  card.classList.remove('is-touch-active');
  card.dataset.controlsDismissed = '';
  const listRow=card.matches('.entry-row');
  if(listRow)card.dataset.editorDeselected='';
  const listeners = new AbortController();
  const reset = () => {
    delete card.dataset.controlsDismissed;
    if(listRow)delete card.dataset.editorDeselected;
    listeners.abort();
    signal.removeEventListener('abort', reset);
  };
  signal.addEventListener('abort', reset, {once:true});
  document.addEventListener('pointermove', event => {
    if (event.pointerType === 'mouse' && (event.movementX || event.movementY) && card.contains(event.target as Node)) reset();
  }, {signal:listeners.signal});
  document.addEventListener('pointerdown', reset, {signal:listeners.signal});
  document.addEventListener('keydown', reset, {signal:listeners.signal});
}
