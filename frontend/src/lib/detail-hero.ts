import {artworkIdentity} from './responsive-artwork.ts';
import type {editorStore} from './editor-store.ts';

export function publishDetailArtwork(store:Pick<typeof editorStore,'updateArtwork'|'updateSubjectArtwork'>,
  info:{id:number;type?:string},artwork:{poster?:string;backdrop?:string}) {
  if(info.type==='game')store.updateSubjectArtwork({kind:'game',catalogue_id:info.id},artwork);
  else if(info.type==='movie'||info.type==='series')store.updateArtwork(info.id,artwork);
}

/** Provider buckets still describe one failed asset, not another fallback. */
export function nextDetailHero(candidates:(string|undefined)[],failed:Set<string>) {
  return candidates.find(url=>!!url&&!failed.has(artworkIdentity(url)));
}

/** Publish only the hero actually displayed, so later editor intent reuses it. */
export function commitDetailHero(hero:HTMLImageElement,candidate:HTMLImageElement,url:string,
  button:HTMLButtonElement|null,committed:(url:string)=>void) {
  if(candidate!==hero){candidate.className=hero.className;candidate.alt='';hero.replaceWith(candidate);}
  candidate.hidden=false;
  if(button){button.dataset.detailImage=url;button.hidden=false;}
  committed(url);
}
