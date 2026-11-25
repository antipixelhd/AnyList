import {browseTab, browseTabHref, type BrowseTab} from './browse-tabs.ts';
import type {MediaDomains} from './media-domains.ts';

export const browseNavigationItems = [
  {tab:'movie', label:'Movies', icon:'film'},
  {tab:'series', label:'Series', icon:'tv'},
  {tab:'game', label:'Games', icon:'gamepad'},
] as const;

export const browseNavigationShortcuts = [
  {key:'trending', label:'Trending'},
  {key:'newest', label:'New releases'},
  {key:'upcoming', label:'Upcoming'},
] as const;

export function browseShortcutHref(tab: BrowseTab, shortcut: (typeof browseNavigationShortcuts)[number]['key']) {
  const values = new URLSearchParams({sort: shortcut});
  if (shortcut !== 'trending') values.set('view', 'timeline');
  if (shortcut === 'upcoming') {
    values.set('status', 'upcoming');
    if (tab === 'game') values.set('sort', 'newest');
  }
  return browseTabHref(tab, values);
}

export function browseNavigationEnabled(tab: BrowseTab, domains: MediaDomains) {
  return tab === 'game' ? domains.show_games && domains.can_access_games !== false : domains.show_movies_series;
}

export function cleanBrowseHref(tab: BrowseTab) {
  return browseTabHref(tab, new URLSearchParams());
}

export function browseHomeHref(domains: MediaDomains) {
  return !domains.show_movies_series && browseNavigationEnabled('game', domains) ? '/browse/games' : '/browse';
}

export function syncBrowseNavigation(nav: HTMLElement, domains: MediaDomains, url: URL = new URL(location.href)) {
  const current = browseTab(url);
  const href = browseHomeHref(domains);
  const targets = document.querySelectorAll<HTMLAnchorElement>('[data-browse-home], [data-mobile-item=browse]');
  targets.forEach(link => {
    link.href = href;
    if (current) link.setAttribute('aria-current', link.hasAttribute('data-browse-home') ? 'location' : 'page');
    else link.removeAttribute('aria-current');
  });
  let visibleChoices = 0;
  nav.querySelectorAll<HTMLAnchorElement>('[data-browse-category]').forEach(link => {
    const category = link.dataset.browseCategory as BrowseTab;
    const enabled = browseNavigationEnabled(category, domains);
    link.closest('li')!.hidden = !enabled;
    if (enabled) visibleChoices++;
    if (enabled && category === current) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  const toggle = nav.querySelector<HTMLButtonElement>('[data-browse-menu-toggle]');
  if (toggle) toggle.hidden = visibleChoices === 0;
}

export function mountBrowseNavigation(nav: HTMLElement, signal: AbortSignal) {
  const group = nav.querySelector<HTMLElement>('[data-appbar-browse]');
  const toggle = group?.querySelector<HTMLButtonElement>('[data-browse-menu-toggle]');
  const menu = group?.querySelector<HTMLElement>('[data-browse-menu]');
  if (!group || !toggle || !menu) return;

  const hover = matchMedia('(any-hover: hover)');
  let openedByDisclosure = false;
  const close = () => {
    openedByDisclosure = false;
    toggle.setAttribute('aria-expanded', 'false');
    menu.inert = true;
  };
  const open = () => {
    if (toggle.hidden) return;
    toggle.setAttribute('aria-expanded', 'true');
    menu.inert = false;
  };
  group.addEventListener('pointerenter', event => { if (hover.matches && event.pointerType !== 'touch') open(); }, {signal});
  group.addEventListener('pointerleave', event => {
    if (event.pointerType !== 'touch' && !openedByDisclosure && !menu.contains(document.activeElement)) close();
  }, {signal});
  toggle.addEventListener('click', event => {
    const hoverClick = hover.matches && event.detail > 0 && ('pointerType' in event
      ? event.pointerType !== 'touch'
      : group.matches(':hover'));
    if (!hoverClick && openedByDisclosure) close();
    else {
      openedByDisclosure = !hoverClick;
      open();
    }
  }, {signal});
  group.addEventListener('focusout', event => {
    if (event.relatedTarget instanceof Node && !group.contains(event.relatedTarget)) close();
  }, {signal});
  document.addEventListener('pointerdown', event => { if (!group.contains(event.target as Node)) close(); }, {signal});
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || event.defaultPrevented || menu.inert) return;
    event.preventDefault();
    if (menu.contains(document.activeElement)) toggle.focus({preventScroll:true});
    close();
  }, {signal});
  signal.addEventListener('abort', close, {once:true});
  return close;
}
