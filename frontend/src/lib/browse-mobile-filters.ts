/** Move the existing controls, preserving values and listeners at each breakpoint. */
export function initializeMobileBrowseFilters(form: HTMLFormElement, signal: AbortSignal) {
  const mobile = matchMedia('(max-width: 650px)');
  const panel = form.querySelector<HTMLElement>('.browse-secondary-filters')!;
  const controls = [...form.querySelectorAll<HTMLElement>(
    '.browse-controls > .browse-filter, .browse-controls > .browse-sort',
  )].map(control => {
    const anchor = document.createComment('browse filter position');
    control.before(anchor);
    return {control, anchor};
  });
  const update = () => {
    if (mobile.matches) panel.prepend(...controls.map(({control}) => control));
    else controls.forEach(({control, anchor}) => anchor.after(control));
    form.toggleAttribute('data-mobile-filters', mobile.matches);
  };
  update();
  mobile.addEventListener('change', update, {signal});
  signal.addEventListener('abort', () => {
    controls.forEach(({control, anchor}) => {
      anchor.after(control);
      anchor.remove();
    });
    form.removeAttribute('data-mobile-filters');
  }, {once: true});
}
