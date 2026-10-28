import { editorStore, type EditorTitle } from './editor-store';
import {showActionFailure} from './action-failure';
import {predictionAllowed} from './predictive-loading';
import {resolveGameId} from './game-discovery';
import {localToday} from './date-time';
import {browseActionLabel,browseStatusLabel,expansionCard} from './browse-card-data';

export type BrowseItem = {
  id: number | null; tmdb_id?: number | null; type: string; title: string;
  igdb_id?:number; game_class?:string;
  poster?: string | null; backdrop?: string | null; year?: string; release_date?: string | null;
  release_event?: string | null; release_season?: number | null; release_country?: string | null;
  list_status?: string | null; score?: number | null; rating_mode?: string;
  favorite?: boolean;
  entry?: Record<string, any> | null; editor_library?: EditorTitle['editor_library'];
};
export const sameBrowseTitle = (a: BrowseItem, b: Pick<EditorTitle, 'id' | 'tmdb_id' | 'type'>) =>
  a.type === b.type && (!!(b.id && a.id === b.id) || !!(b.tmdb_id && a.tmdb_id === b.tmdb_id));

export function paintBrowseCardState(card: HTMLElement, item: BrowseItem) {
  card.querySelector('[data-browse-action=edit]')?.setAttribute('aria-label',`Open list editor for ${item.title}`);
  card.dataset.state = item.list_status || '';
  card.querySelectorAll<HTMLElement>('[data-editor-icon]').forEach(icon => icon.hidden = icon.dataset.editorIcon === 'edit' ? !item.list_status : !!item.list_status);
  const score = Number(item.score || 0).toFixed(1);
  const rating = card.querySelector<HTMLElement>('[data-user-score]')!;
  rating.hidden = !(item.score != null && item.score > 0);
  rating.setAttribute('aria-label', `Your rating: ${score} out of 10`);
  rating.querySelector('[data-score]')!.textContent = score;
  const state = card.querySelector<HTMLElement>('[data-list-state]')!;
  const game=item.type==='game';
  state.hidden = !item.list_status; state.title = browseStatusLabel(item.list_status||'',game);
  state.setAttribute('aria-label', state.title);
  state.querySelectorAll<HTMLElement>('[data-state-icon]').forEach(icon => icon.hidden = icon.dataset.stateIcon !== item.list_status);
  for (const action of ['watch','plan','rate']) {
    const button = card.querySelector<HTMLButtonElement>(`[data-browse-action=${action}]`);
    if (button){
      button.hidden = expansionCard(item)||(action === 'watch' ? item.list_status === (game?'playing':'watching') : action === 'plan' ? !!item.list_status : item.score != null && item.score > 0);
      button.setAttribute('aria-label',`${browseActionLabel(action,game)} ${item.title}`);
      if(action!=='rate')button.dataset.tooltip=browseActionLabel(action,game);
    }
  }
  const extras=card.querySelector<HTMLElement>('.browse-extra-actions');if(extras)extras.hidden=expansionCard(item);
}

export function bindBrowseCardActions<T extends BrowseItem>(
  region: HTMLElement, items: Map<HTMLElement, T>, signal: AbortSignal,
  onUpdate: (item: T) => void, onError: (message: string) => void,
) {
  const titleFor = (item: T) => editorStore.get({...item, entry:item.entry ?? null});
  const gameReads=new Map<number,Promise<EditorTitle>>();
  const gameTitle=async(item:T)=>{
    const key=item.igdb_id||-item.id!;
    const existing=gameReads.get(key);if(existing)return existing;
    const read=(async()=>{
      const id=item.id||await resolveGameId(item.igdb_id!,signal);
      if(signal.aborted)throw new Error('Browse changed.');
      for(const other of items.values())if(other.type==='game'&&(other.igdb_id===item.igdb_id&&item.igdb_id||other.id===id))other.id=id;
      const title=await editorStore.loadSubject({kind:'game',catalogue_id:id});
      if(!signal.aborted)onUpdate({...item,id,list_status:title.entry?.status,score:title.entry?.score});
      return title;
    })();
    gameReads.set(key,read);
    try{return await read;}finally{if(gameReads.get(key)===read)gameReads.delete(key);}
  };
  let warmTimer:ReturnType<typeof setTimeout>|undefined;
  let warmCard:HTMLElement|undefined;
  const clearWarm=()=>{clearTimeout(warmTimer);warmCard=undefined;};
  const warm = (event: Event) => {
    if(event instanceof PointerEvent&&event.pointerType!=='mouse')return;
    const card = (event.target as Element).closest<HTMLElement>('[data-browse-card]');
    const item = card && items.get(card);
    if (item && card && card.querySelector('[data-browse-action]')&&warmCard!==card) {
      clearWarm();warmCard=card;
      warmTimer=setTimeout(()=>{
        if(signal.aborted||!card.isConnected||!predictionAllowed())return;
        const image = card.querySelector<HTMLImageElement>('[data-poster]');
        const title=item.type==='game'?item.id?editorStore.findSubject({kind:'game',catalogue_id:item.id}):undefined:titleFor(item);
        if(title)editorStore.warmArtwork(title, image?.currentSrc || image?.src);
      },180);
    }
  };
  region.addEventListener('pointerover', warm, {signal});
  region.addEventListener('focusin', warm, {signal});
  region.addEventListener('pointerout',event=>{if(warmCard&&!warmCard.contains((event as PointerEvent).relatedTarget as Node|null))clearWarm();},{signal});
  region.addEventListener('focusout',clearWarm,{signal});
  signal.addEventListener('abort',clearWarm,{once:true});
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
    let wasNew = !item.list_status;
    const saved = (entry: any) => { if (!signal.aborted) document.dispatchEvent(new CustomEvent('anylist:entry-saved', {detail:entry})); };
    try {
      const game=item.type==='game';if(game)button.disabled=true;
      const title=game?await gameTitle(item):titleFor(item);
      wasNew = !title.entry;
      if(signal.aborted)return;
      if(game)editorStore.warmArtwork(title,card.querySelector<HTMLImageElement>('[data-poster]')?.currentSrc);
      if (button.dataset.browseAction === 'edit'||expansionCard(item)) {
        document.dispatchEvent(new CustomEvent('anylist:open-editor', {detail:game?{subject:title.subject,opener:button,title}:{mediaId:item.id,opener:button,title}}));
      } else if (button.dataset.browseAction === 'plan' || button.dataset.browseAction === 'watch') {
        button.disabled = true;
        const status=button.dataset.browseAction==='watch'?(game?'playing':'watching'):'planning';
        saved(await editorStore.write(title, {status,...(game&&status==='playing'&&title.entry?.status!=='playing'&&!title.entry?.start_date?{start_date:localToday()}:{} )}));
      } else {
        (window as any).anyListQuickRate?.({title:item.title,poster:item.poster,posterSrc:card.querySelector<HTMLImageElement>('[data-poster]')?.currentSrc,
          score:title.entry?.score,ratingMode:title.entry?.rating_mode || 'manual',
          onScore: async (score: number | null) => saved(await editorStore.write(title, game?{score,...(score!=null?{status:'completed',...(title.entry?.status!=='completed'&&!title.entry?.finish_date?{finish_date:localToday()}:{})}:{})}:{manual_score:score ?? 0,...(score != null ? {status:'completed',rating_mode:'manual'} : {})})),
        });
      }
    } catch (error) { if (!signal.aborted) showActionFailure(wasNew ? 'add' : 'status', item.title, error); }
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
