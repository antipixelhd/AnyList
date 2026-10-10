export function mountStatisticsNavigation(root: HTMLElement, signal: AbortSignal) {
  const button = root.querySelector<HTMLButtonElement>('[data-stats-menu-toggle]')!;
  const sidebar = root.querySelector<HTMLElement>('.overview-sidebar')!;
  const mobile = matchMedia('(max-width: 760px)');
  function setOpen(open: boolean, restoreFocus = false) {
    sidebar.dataset.menuOpen = String(open);
    button.setAttribute('aria-expanded', String(open));
    button.setAttribute('aria-label', `${open ? 'Hide' : 'Show'} statistics sections`);
    if (restoreFocus && mobile.matches) button.focus();
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
}
