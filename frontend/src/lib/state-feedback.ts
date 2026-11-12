const active = new Map<HTMLElement, () => void>();

function play(element: HTMLElement, icon: boolean, selected: boolean, signal?: AbortSignal) {
  const doc = element.ownerDocument, view = doc.defaultView;
  if (signal?.aborted || !view || !element.isConnected || typeof element.animate !== 'function' ||
    view.matchMedia('(prefers-reduced-motion: reduce)').matches || element.closest('[inert]')) return;
  const bounds = element.getBoundingClientRect();
  if (!bounds.width || !bounds.height || bounds.bottom <= 0 || bounds.top >= view.innerHeight) return;
  const previous = active.get(element), style = previous && view.getComputedStyle(element);
  const scale = style?.scale && style.scale !== 'none' ? style.scale : '1';
  const translate = style?.translate && style.translate !== 'none' ? style.translate : '0 3px';
  const opacity = style?.opacity || '.45';
  previous?.();
  const animation = element.animate(icon ? {
    scale: [previous ? scale : selected ? '.86' : '1', selected ? '1.16' : '.86', '1'],
    transformOrigin: ['50% 50%', '50% 50%', '50% 50%'],
  } : {opacity: [opacity, '1'], translate: [translate, '0 0']}, {
    duration: 280, easing: 'cubic-bezier(.2,.7,.2,1)',
  });
  if (doc.timeline.currentTime !== null) animation.startTime = doc.timeline.currentTime;
  const clear = () => {
    if (active.get(element) === clear) active.delete(element);
    signal?.removeEventListener('abort', clear);
    animation.cancel();
  };
  active.set(element, clear);
  signal?.addEventListener('abort', clear, {once:true});
  void animation.finished.then(clear, clear);
}

export function setFeedbackText(element: Element | null, value: string, acknowledge = false, signal?: AbortSignal) {
  if (!element || element.textContent === value) return;
  const previous = element.textContent;
  element.textContent = value;
  if (acknowledge && previous) play(element as HTMLElement, false, false, signal);
}

export function setFeedbackPressed(button: HTMLElement | null, selected: boolean, acknowledge = false, signal?: AbortSignal) {
  if (!button) return;
  const previous = button.getAttribute('aria-pressed');
  button.setAttribute('aria-pressed', String(selected));
  if (acknowledge && previous !== null && previous !== String(selected)) {
    const icon = button.querySelector<HTMLElement>('.ui-icon');
    if (icon) play(icon, true, selected, signal);
  }
}

export function cancelStateFeedback() {
  for (const clear of active.values()) clear();
}
