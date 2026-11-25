import {applyResponsiveArtwork} from './responsive-artwork';
import {prepareBrowsePoster} from './browse-loading';
import {paintBrowseCardState,type BrowseItem} from './browse-card-actions';
import {browseCardHref,browseCardKey,browseSeasonCaption} from './browse-card-data';

/** The same poster, typography, controls and image lifecycle for every browse domain. */
export function createBrowseCard(item:BrowseItem,template:HTMLTemplateElement,signal:AbortSignal,readyPosters=new Set<string>()){
  const card = template.content.firstElementChild!.cloneNode(
    true,
  ) as HTMLElement;
  card.dataset.key = browseCardKey(item);
  card.dataset.detailKind=item.type;
  const href=browseCardHref(item)||'#';
  card
    .querySelectorAll<HTMLAnchorElement>("a")
    .forEach((a) => (a.href = href));
  const title = item.series_title || item.title;
  const season = browseSeasonCaption(item.series_season);
  const seasonTitle = season ? `${title}, ${season.label}` : undefined;
  card.querySelector("h2")!.textContent = title;
  card
    .querySelector(".browse-poster-link")!
    .setAttribute("aria-label", seasonTitle || title);
  if (seasonTitle) card.querySelector(".browse-card-title")!.setAttribute("aria-label", seasonTitle);
  const img = card.querySelector<HTMLImageElement>("[data-poster]")!;
  img.hidden = !item.poster;
  if (item.poster)
    applyResponsiveArtwork(img, item.poster, {
      size: "w342",
      sizes: "(max-width: 650px) 30vw, (max-width: 1000px) 22vw, 185px",
      route:'auto',
    });
  prepareBrowsePoster(img, signal, readyPosters);
  const placeholder = card.querySelector<HTMLElement>(
    ".browse-placeholder",
  )!;
  placeholder.hidden = !!item.poster;
  placeholder.textContent = item.title.slice(0, 1);
  if (season) {
    const seasonLabel = card.querySelector<HTMLElement>('[data-browse-season]')!;
    seasonLabel.hidden = false;
    seasonLabel.textContent = season.label;
    if (season.dateLabel && season.date) {
      const seasonDate = card.querySelector<HTMLTimeElement>('[data-browse-season-date]')!;
      seasonDate.hidden = false;
      seasonDate.dateTime = season.date;
      seasonDate.textContent = season.dateLabel;
    }
  }
  const year = card.querySelector<HTMLElement>('[data-year]')!;
  year.hidden = !!season;
  year.textContent = item.year || "";
  if (item.release_event) year.title = item.release_country ? `Release date · ${item.release_country}` : 'Worldwide release date';
  paintBrowseCardState(card,item);

  return card;
}
