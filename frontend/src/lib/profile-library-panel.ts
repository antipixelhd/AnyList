import {showActionFailure} from './action-failure';
export function initializeLibrary(root:HTMLElement, signal:AbortSignal) {
  const page = root.querySelector<HTMLElement>('.library-page');
  if (!page) return;
  const error = page.querySelector<HTMLElement>('[data-library-error]')!;
  page.addEventListener('click', async event => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>('[data-library-remove]');
    if (!button) return;
    button.disabled = true;
    page.dataset.profileWriting='';
    error.hidden = true;
    try {
      const response = await fetch(`/api/proxy/tracking/library/${button.dataset.libraryRemove}`, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({in_library: false}),
      });
      if (!response.ok) {
        const details = await response.json().catch(() => null);
        throw new Error(typeof details?.detail === 'string' ? details.detail : 'Could not remove this title from your library.');
      }
      const card = button.closest<HTMLElement>('[data-library-entry]')!;
      const group = card.closest<HTMLElement>('[data-library-group]')!;
      card.remove();
      group.hidden = !group.querySelector('[data-library-entry]');
      const hasEntries = !!page.querySelector('[data-library-entry]');
      page.querySelector<HTMLElement>('.library-sections')!.hidden = !hasEntries;
      page.querySelector<HTMLElement>('[data-library-empty]')!.hidden = hasEntries;
    } catch (cause) {
      showActionFailure('library',button.closest<HTMLElement>('[data-library-entry]')?.dataset.title,cause);
      button.disabled = false;
    } finally {
      if(!page.querySelector('[data-library-remove]:disabled'))delete page.dataset.profileWriting;
      root.dispatchEvent(new Event('anylist:profile-refresh',{bubbles:true}));
    }
  }, {signal: signal});
}
