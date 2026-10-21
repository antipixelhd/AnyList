import {applyResponsiveArtwork} from './responsive-artwork';
import {prepareBrowsePoster} from './browse-loading';
import {paintBrowseCardState,type BrowseItem} from './browse-card-actions';
import {browseCardHref,browseCardKey} from './browse-card-data';

/** The same poster, typography, controls and image lifecycle for every browse domain. */
export function createBrowseCard(item:BrowseItem,template:HTMLTemplateElement,signal:AbortSignal,readyPosters=new Set<string>()){
  const card = template.content.firstElementChild!.cloneNode(
    true,
  ) as HTMLElement;
  card.dataset.key = browseCardKey(item);
  const href=browseCardHref(item)||'#';
  card
    .querySelectorAll<HTMLAnchorElement>("a")
    .forEach((a) => (a.href = href));
  card.querySelector("h2")!.textContent = item.title;
  card
    .querySelector(".browse-poster-link")!
    .setAttribute("aria-label", item.title);
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
  card.querySelector("[data-year]")!.textContent = item.year || "";
  if (item.release_event) card.querySelector<HTMLElement>('[data-year]')!.title = item.release_country ? `Release date · ${item.release_country}` : 'Worldwide release date';
  paintBrowseCardState(card,item);

  return card;
}
