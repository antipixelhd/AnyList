import { editorStore, type EditorTitle } from './editor-store';

export type BrowseItem = {
  id: number | null; tmdb_id: number | null; type: string; title: string;
  poster?: string | null; backdrop?: string | null; year?: string;
  list_status?: string | null; score?: number | null; rating_mode?: string;
  entry?: Record<string, any> | null; editor_library?: EditorTitle['editor_library'];
};
const labels: Record<string, string> = {watching:'Watching',completed:'Completed',planning:'Plan to Watch',paused:'Paused',dropped:'Dropped'};
export const sameBrowseTitle = (a: BrowseItem, b: Pick<EditorTitle, 'id' | 'tmdb_id' | 'type'>) =>
  !!(b.id && a.id === b.id) || !!(b.tmdb_id && a.tmdb_id === b.tmdb_id && a.type === b.type);

export function paintBrowseCardState(card: HTMLElement, item: BrowseItem) {
  card.dataset.state = item.list_status || '';
  card.querySelectorAll<HTMLElement>('[data-editor-icon]').forEach(icon => icon.hidden = icon.dataset.editorIcon === 'edit' ? !item.list_status : !!item.list_status);
  const score = Number(item.score || 0).toFixed(1);
  const rating = card.querySelector<HTMLElement>('[data-user-score]')!;
  rating.hidden = !(item.score != null && item.score > 0);
  rating.setAttribute('aria-label', `Your rating: ${score} out of 10`);
  rating.querySelector('[data-score]')!.textContent = score;
  const state = card.querySelector<HTMLElement>('[data-list-state]')!;
  state.hidden = !item.list_status; state.title = labels[item.list_status || ''] || '';
  state.setAttribute('aria-label', state.title);
  state.querySelectorAll<HTMLElement>('[data-state-icon]').forEach(icon => icon.hidden = icon.dataset.stateIcon !== item.list_status);
  for (const action of ['watch','plan','rate']) {
    const button = card.querySelector<HTMLButtonElement>(`[data-browse-action=${action}]`);
    if (button) button.hidden = action === 'watch' ? item.list_status === 'watching' : action === 'plan' ? !!item.list_status : item.score != null && item.score > 0;
  }
}

export function bindBrowseCardActions<T extends BrowseItem>(
  region: HTMLElement, items: Map<HTMLElement, T>, signal: AbortSignal,
  onUpdate: (item: T) => void, onError: (message: string) => void,
) {
  const titleFor = (item: T) => editorStore.get({...item, entry:item.entry ?? null});
  const warm = (event: Event) => {
    const card = (event.target as Element).closest<HTMLElement>('[data-browse-card]');
    const item = card && items.get(card);
    if (item && card && card.querySelector('[data-browse-action]')) {
      const image = card.querySelector<HTMLImageElement>('[data-poster]');
      editorStore.warmArtwork(titleFor(item), image?.currentSrc || image?.src);
    }
  };
  region.addEventListener('pointerover', warm, {signal});
  region.addEventListener('focusin', warm, {signal});
  document.addEventListener('anylist:editor-state', event => {
    const title = (event as CustomEvent<EditorTitle>).detail;
    const item = [...items.values()].find(item => sameBrowseTitle(item, title));
    if (item) onUpdate({...item,id:title.id,entry:title.entry,list_status:title.entry?.status,score:title.entry?.score,rating_mode:title.entry?.rating_mode});
  }, {signal});
  region.addEventListener('click', async event => {
    const button = (event.target as Element).closest<HTMLButtonElement>('[data-browse-action]');
    const card = button?.closest<HTMLElement>('[data-browse-card]');
    const item = card && items.get(card);
    if (!button || !card || !item || button.disabled) return;
    const title = titleFor(item);
    const saved = (entry: any) => { if (!signal.aborted) document.dispatchEvent(new CustomEvent('anylist:entry-saved', {detail:entry})); };
    try {
      if (button.dataset.browseAction === 'edit') {
        document.dispatchEvent(new CustomEvent('anylist:open-editor', {detail:{mediaId:item.id,opener:button,title}}));
      } else if (button.dataset.browseAction === 'plan' || button.dataset.browseAction === 'watch') {
        button.disabled = true;
        saved(await editorStore.write(title, {status:button.dataset.browseAction === 'watch' ? 'watching' : 'planning'}));
      } else {
        (window as any).anyListQuickRate?.({title:item.title,poster:item.poster,posterSrc:card.querySelector<HTMLImageElement>('[data-poster]')?.currentSrc,
          score:title.entry?.score,ratingMode:title.entry?.rating_mode || 'manual',
          onScore: async (score: number | null) => saved(await editorStore.write(title, {manual_score:score ?? 0,...(score != null ? {status:'completed',rating_mode:'manual'} : {})})),
        });
      }
    } catch (error) { if (!signal.aborted) onError(error instanceof Error ? error.message : 'Could not update your list.'); }
    finally { if (!signal.aborted) button.disabled = false; }
  }, {signal});
}

export function bindBrowseCardTouch(region: HTMLElement, signal: AbortSignal) {
  let touch = false;
  region.addEventListener('pointerdown', event => { touch = event.pointerType === 'touch'; }, {signal});
  region.addEventListener('click', event => {
    if (!touch) return;
    const card = (event.target as Element).closest('.browse-poster-link')?.closest('[data-browse-card]');
    if (!card?.querySelector('.browse-actions') || card.classList.contains('is-touch-active')) return;
    event.preventDefault();
    region.querySelectorAll('.is-touch-active').forEach(other => other.classList.remove('is-touch-active'));
    card.classList.add('is-touch-active');
  }, {signal});
  document.addEventListener('pointerdown', event => {
    region.querySelectorAll('.is-touch-active').forEach(card => {
      if (!card.contains(event.target as Node)) {
        card.classList.remove('is-touch-active');
        if (card.contains(document.activeElement)) (document.activeElement as HTMLElement)?.blur();
      }
    });
  }, {signal});
}
