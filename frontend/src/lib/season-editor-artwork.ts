import {artworkIdentity, responsiveArtwork} from './responsive-artwork';
import type {SeriesEditorSummary} from './series-editor';

type SeasonArtworkHint = {
  number:number; order?:string; poster?:string; posterSource?:string;
};

export function seasonArtworkHint(opener: HTMLElement): SeasonArtworkHint | undefined {
  const number = Number(opener.dataset.editorSeason);
  if (!number) return;
  const image = opener.closest('.entry-thumbnail')?.querySelector<HTMLImageElement>('img');
  return {number, order:opener.dataset.editorSeasonOrder, poster:opener.dataset.editorSeasonPoster,
    posterSource:image?.currentSrc || image?.src};
}

export function seasonPosterSource(number:number, summary?:Pick<SeriesEditorSummary,'order'|'seasons'>,
  hint?:SeasonArtworkHint):string | undefined {
  if (!number) return;
  const season = summary?.seasons.find(season=>season.season_number===number);
  const row = hint?.number===number && (!summary?.order || !hint.order || summary.order===hint.order) ? hint : undefined;
  const poster = season?.poster || row?.poster;
  const displayed = row?.posterSource;
  const samePoster = poster && displayed && artworkIdentity(poster)===artworkIdentity(displayed);
  return samePoster ? displayed : responsiveArtwork(poster,{size:'w342',route:'direct'})?.src;
}
