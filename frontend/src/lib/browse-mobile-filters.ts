/** Move the existing controls, preserving values and listeners at each breakpoint. */
export function initializeMobileBrowseFilters(form: HTMLFormElement, signal: AbortSignal) {
  const mobile = matchMedia('(max-width: 650px)');
  const panel = form.querySelector<HTMLElement>('.browse-secondary-filters')!;
  const controls = [...form.querySelectorAll<HTMLElement>(
    '.browse-controls > .browse-filter, .browse-controls > .browse-sort',
  )].map(control => {
    const anchor = document.createComment('browse filter position');
    control.dataset.browseFilterPosition='';
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
      delete control.dataset.browseFilterPosition;
    });
    form.removeAttribute('data-mobile-filters');
  }, {once: true});
}

export function resetSavedBrowseFilterPositions(root:HTMLElement) {
  const controls=root.querySelector('.browse-controls');
  if(!controls)return;
  const anchors=[...controls.childNodes].filter((node):node is Comment=>node instanceof Comment&&node.data==='browse filter position');
  if(root.querySelector('[data-mobile-filters]')){
    const moved=[...root.querySelectorAll<HTMLElement>('.browse-secondary-filters > [data-browse-filter-position]')];
    moved.forEach((control,index)=>anchors[index]?.after(control));
  }
  anchors.forEach(anchor=>anchor.remove());
  root.querySelectorAll<HTMLElement>('[data-browse-filter-position]').forEach(control=>delete control.dataset.browseFilterPosition);
}
