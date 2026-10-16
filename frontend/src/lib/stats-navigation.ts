import { cancelUiMotion, hideMenu, showMenu } from './ui-motion';

export function mountStatisticsNavigation(root: HTMLElement, signal: AbortSignal) {
  const button = root.querySelector<HTMLButtonElement>('[data-stats-menu-toggle]')!;
  const sidebar = root.querySelector<HTMLElement>('.overview-sidebar')!;
  const mobile = matchMedia('(max-width: 760px)');
  const menu = sidebar.querySelector<HTMLElement>('#statistics-sections')!;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  let heightMotion: Animation | undefined;
  const settleHeight = () => { heightMotion?.cancel(); heightMotion = undefined; sidebar.style.removeProperty('overflow'); };
  sidebar.dataset.menuScripted = '';
  function setOpen(open: boolean, restoreFocus = false) {
    const start = sidebar.getBoundingClientRect().height;
    settleHeight();
    const changed = button.getAttribute('aria-expanded') !== String(open);
    sidebar.dataset.menuOpen = String(open);
    button.setAttribute('aria-expanded', String(open));
    button.setAttribute('aria-label', `${open ? 'Hide' : 'Show'} statistics sections`);
    if (restoreFocus && mobile.matches) button.focus();
    if (!mobile.matches) {
      showMenu(menu, true);
    } else if (changed) {
      if (open) showMenu(menu); else void hideMenu(menu);
    } else if (!open) { cancelUiMotion(menu); menu.hidden = true; menu.inert = true; }
    if (changed && mobile.matches && !reduced.matches) {
      const end = open ? sidebar.getBoundingClientRect().height : button.getBoundingClientRect().height;
      sidebar.style.overflow = 'hidden';
      const current = sidebar.animate([{height:`${start}px`},{height:`${end}px`}],{duration:260,easing:'cubic-bezier(.2,.7,.2,1)',fill:'both'});
      heightMotion = current;
      current.finished.then(() => { if (heightMotion === current) settleHeight(); }).catch(() => {});
    }
  }
  setOpen(false);
  button.addEventListener('click', () => setOpen(button.getAttribute('aria-expanded') !== 'true'), { signal });
  root.addEventListener('profile-section-change', () => setOpen(false, true), { signal });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && button.getAttribute('aria-expanded') === 'true') setOpen(false, true);
  }, { signal });
  document.addEventListener('click', event => {
    if (event.target instanceof Node && !sidebar.contains(event.target)) setOpen(false);
  }, { signal });
  mobile.addEventListener('change', () => setOpen(false), { signal });
  window.addEventListener('popstate', () => setOpen(false), { signal });
  reduced.addEventListener('change', () => {
    settleHeight();
    const open = button.getAttribute('aria-expanded') === 'true';
    showMenu(menu, true); menu.hidden = mobile.matches && !open; menu.inert = menu.hidden;
  }, {signal});
  signal.addEventListener('abort', () => { settleHeight(); cancelUiMotion(menu); }, {once:true});
}
