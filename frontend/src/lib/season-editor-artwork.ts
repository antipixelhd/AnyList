import {artworkIdentity, responsiveArtwork} from './responsive-artwork';
import type {SeriesEditorSummary} from './series-editor';

export type ArtworkSources = {poster?:string; backdrop?:string};
type SeasonArtworkHint = {
  number:number; order?:string; poster?:string; backdrop?:string; posterSource?:string;
};

export function seasonArtworkHint(opener: HTMLElement): SeasonArtworkHint | undefined {
  const number = Number(opener.dataset.editorSeason);
  if (!number) return;
  const image = opener.closest('.entry-thumbnail')?.querySelector<HTMLImageElement>('img');
  return {number, order:opener.dataset.editorSeasonOrder, poster:opener.dataset.editorSeasonPoster,
    backdrop:opener.dataset.editorSeasonBackdrop, posterSource:image?.currentSrc || image?.src};
}

export function seasonArtworkSources(number:number, summary?:Pick<SeriesEditorSummary,'order'|'seasons'>,
  hint?:SeasonArtworkHint):ArtworkSources {
  if (!number) return {};
  const season = summary?.seasons.find(season=>season.season_number===number);
  const row = hint?.number===number && (!summary?.order || !hint.order || summary.order===hint.order) ? hint : undefined;
  const poster = season?.poster || row?.poster;
  const displayed = row?.posterSource;
  const samePoster = poster && displayed && artworkIdentity(poster)===artworkIdentity(displayed);
  const backdrop = season?.backdrop || row?.backdrop;
  return {poster:samePoster ? displayed : responsiveArtwork(poster,{size:'w342',route:'direct'})?.src,
    backdrop:responsiveArtwork(backdrop,{size:'w780',route:'direct'})?.src};
}
