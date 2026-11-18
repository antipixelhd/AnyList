import { appearanceSeasonText, contributorWorkTitle, type ContributorWork } from './contributor-page-data';

export function paintWorkAppearance(card: HTMLElement, item: ContributorWork) {
  card.querySelector('h2')!.textContent = contributorWorkTitle(item);
  card.querySelector('.browse-card-title-row')!.toggleAttribute('data-appearance', !!item.appearance);
  const trigger = card.querySelector<HTMLButtonElement>('[data-appearance-trigger]')!;
  const panel = card.querySelector<HTMLElement>('[data-appearance-seasons]')!;
  const seasons = item.appearance?.seasons || [];
  trigger.hidden = seasons.length < 2;
  if (trigger.hidden) {
    panel.removeAttribute('id');
    trigger.removeAttribute('popovertarget');
    return;
  }
  panel.id = `appearance-${item.tmdb_id}`;
  trigger.setAttribute('popovertarget', panel.id);
  panel.replaceChildren(...seasons.map(season => {
    const line = document.createElement('li');
    line.textContent = appearanceSeasonText(season);
    return line;
  }));
}

export function mountContributorAppearances(region: HTMLElement, signal: AbortSignal) {
  const native = 'showPopover' in HTMLElement.prototype;
  let active: HTMLElement | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const options = {signal};
  const panelOf = (target: EventTarget | null) => {
    const row = target instanceof Element && target.closest('.browse-card-title-row[data-appearance]');
    return row && row.querySelector('[data-appearance-trigger]:not([hidden])')
      ? row.querySelector<HTMLElement>('[data-appearance-seasons]') : null;
  };
  const position = (panel: HTMLElement) => {
    const row = panel.parentElement!.getBoundingClientRect();
    const above = row.top >= 150 && row.top > innerHeight - row.bottom;
    const width = Math.min(320, innerWidth - 24);
    panel.style.setProperty('--appearance-left', `${Math.min(Math.max(12, row.left), innerWidth - width - 12)}px`);
    panel.style.setProperty('--appearance-room', `${Math.max(60, (above ? row.top : innerHeight - row.bottom) - 12)}px`);
    panel.style.setProperty('--appearance-top', above ? 'auto' : `${row.bottom}px`);
    panel.style.setProperty('--appearance-bottom', above ? `${innerHeight - row.top}px` : 'auto');
    panel.dataset.positioned = '';
  };
  const close = () => {
    clearTimeout(timer);
    if (native && active?.isConnected) active.hidePopover();
    else active?.removeAttribute('data-open');
    active = null;
  };
  const show = (panel: HTMLElement) => {
    if (!panel.isConnected || active === panel) return;
    if (native && document.querySelector('[popover]:popover-open:not([data-appearance-seasons])')) return;
    close();
    if (native) panel.showPopover();
    else {
      position(panel);
      panel.dataset.open = '';
      active = panel;
    }
  };
  if (native) {
    region.addEventListener('beforetoggle', event => {
      const panel = event.target;
      if (!(panel instanceof HTMLElement) || !panel.hasAttribute('data-appearance-seasons')) return;
      if ((event as ToggleEvent).newState === 'open') {
        position(panel);
        active = panel;
      } else if (active === panel) active = null;
    }, {capture:true, signal});
  } else {
    region.addEventListener('click', event => {
      if (!(event.target instanceof Element) || !event.target.closest('[data-appearance-trigger]')) return;
      const panel = panelOf(event.target);
      if (panel) show(panel);
    }, options);
    document.addEventListener('pointerdown', event => {
      if (panelOf(event.target) !== active) close();
    }, options);
    region.addEventListener('keydown', event => { if (event.key === 'Escape') close(); }, options);
  }
  region.addEventListener('pointerover', event => {
    if (event.pointerType !== 'mouse') return;
    const panel = panelOf(event.target);
    if (!panel || panelOf(event.relatedTarget) === panel) return;
    clearTimeout(timer);
    timer = setTimeout(() => show(panel), 160);
  }, options);
  region.addEventListener('pointerout', event => {
    const panel = panelOf(event.target);
    if (!panel || panelOf(event.relatedTarget) === panel) return;
    clearTimeout(timer);
    if (active === panel && !panel.parentElement?.contains(document.activeElement)) close();
  }, options);
  document.addEventListener('scroll', () => { if (active) position(active); }, {capture:true, passive:true, signal});
  window.addEventListener('resize', () => { if (active) position(active); }, options);
  signal.addEventListener('abort', close, {once:true});
}
