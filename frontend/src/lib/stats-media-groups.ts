import type { MediaGroup, Overview } from './stats-overview-data';
import { displayNumber } from './stats-overview-data';
import { genreSort, genreTime } from './stats-genres-data';
import { responsiveArtwork } from './responsive-artwork';

import { carousel } from './stats-carousel';

interface GroupOptions<T extends MediaGroup> {
  section: 'genres' | 'studios'; prefix: 'genre' | 'studio'; parameter: string;
  rows: (data: Overview) => T[];
  href: (row: T, data: Overview) => string;
}
export function mountMediaGroups<T extends MediaGroup>(root: HTMLElement, options: GroupOptions<T>) {
  const { section, prefix, parameter } = options;
  const grid = root.querySelector<HTMLElement>(`[data-${section}-grid]`)!;
  const cardTemplate = root.querySelector<HTMLTemplateElement>(`[data-${prefix}-card-template]`)!;
  const posterTemplate = root.querySelector<HTMLTemplateElement>('[data-genre-poster-template]')!;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)'), events = new AbortController();
  const carousels = new Map<HTMLElement, ReturnType<typeof carousel>>();
  let overview: Overview | null = null;
  const animations = new Map<HTMLElement, Animation>();
  const query = <T extends HTMLElement = HTMLElement>(card: HTMLElement, selector: string) => card.querySelector<T>(selector)!;
  function createCard(row: T) {
    const card = cardTemplate.content.firstElementChild!.cloneNode(true) as HTMLElement;
    card.dataset[`${prefix}Key`] = row.key; card.dataset[`${prefix}Signature`] = JSON.stringify(row); card.setAttribute('aria-label', row.label);
    query(card, `[data-${prefix}-link]`).textContent = row.label;
    query(card, `[data-${prefix}-count]`).textContent = displayNumber(row.titles);
    query(card, `[data-${prefix}-score]`).textContent = displayNumber(row.mean_score, 2);
    query(card, `[data-${prefix}-time]`).textContent = genreTime(row);
    query(card, `[data-${prefix}-no-ratings]`).hidden = row.top_titles.length > 0;
    const track = query(card, '[data-genre-track]'); track.setAttribute('aria-label', `Highest-rated ${row.label} titles`);
    track.replaceChildren(...row.top_titles.map(title => {
      const link = posterTemplate.content.firstElementChild!.cloneNode(true) as HTMLAnchorElement;
      link.href = title.href; link.setAttribute('aria-label', title.title);
      const img = query<HTMLImageElement>(link, 'img'), placeholder = query(link, '.stats-genre-placeholder');
      const art = responsiveArtwork(title.poster, { size: 'w185', sizes: '(max-width:650px) 20vw, 90px', route: 'direct' });
      if (art) { img.src = art.src; if (art.srcset) img.srcset = art.srcset; if (art.sizes) img.sizes = art.sizes; placeholder.remove(); }
      else { img.remove(); placeholder.textContent = title.title.slice(0, 1); }
      query(link, '.stats-genre-tooltip').textContent = title.title;
      return link;
    }));
    query(card, `[data-${prefix}-scroll="-1"]`).setAttribute('aria-label', `Previous ${row.label} titles`);
    query(card, `[data-${prefix}-scroll="1"]`).setAttribute('aria-label', `Next ${row.label} titles`);
    return card;
  }
  function render(next: Overview | null, animate = false) {
    overview = next;
    const before = new Map([...grid.children].map(card => [card, card.getBoundingClientRect()]));
    animations.forEach(animation => animation.cancel()); animations.clear();
    const existing = new Map([...grid.querySelectorAll<HTMLElement>(`[data-${prefix}-key]`)].map(card => [card.dataset[`${prefix}Key`], card]));
    const rows = next ? options.rows(next) : [];
    const cards = rows.map((row, index) => {
      let card = existing.get(row.key);
      if (!card || card.dataset[`${prefix}Signature`] !== JSON.stringify(row)) card = createCard(row);
      const link = query<HTMLAnchorElement>(card, `[data-${prefix}-link]`); link.href = options.href(row, next!);
      const rank = query(card, `[data-${prefix}-rank]`); rank.textContent = String(index + 1); rank.setAttribute('aria-label', `Rank ${index + 1}`);
      return card;
    });
    for (const [card, controller] of carousels) if (!cards.includes(card)) { controller.stop(); carousels.delete(card); }
    grid.replaceChildren(...cards);
    cards.forEach(card => {
      if (!carousels.has(card)) carousels.set(card, carousel(query(card, '[data-genre-track]'), reduced));
      else carousels.get(card)!.bounds();
      if (animate && !reduced.matches && root.dataset.section === section) {
        const old = before.get(card), now = card.getBoundingClientRect();
        const frames = old ? [{ transform: `translate(${old.left - now.left}px,${old.top - now.top}px)` }, { transform: 'translate(0,0)' }] : [{ opacity: 0 }, { opacity: 1 }];
        const animation = card.animate(frames, { duration: 600, easing: 'cubic-bezier(.22,.61,.36,1)' }); animations.set(card, animation);
        animation.onfinish = () => animations.delete(card);
      }
    });
    root.querySelector<HTMLElement>(`[data-${section}-empty]`)!.hidden = rows.length > 0;
    root.querySelectorAll<HTMLButtonElement>(`button[data-${prefix}-sort]`).forEach(button => button.setAttribute('aria-pressed', String(button.dataset[`${prefix}Sort`] === root.dataset[`${prefix}Sort`])));
  }
  root.addEventListener('click', event => {
    const button = (event.target as Element).closest<HTMLButtonElement>(`button[data-${prefix}-sort],[data-${prefix}-scroll]`);
    if (!button) return;
    if (button.dataset[`${prefix}Sort`]) {
      root.dataset[`${prefix}Sort`] = genreSort(button.dataset[`${prefix}Sort`]);
      const url = new URL(location.href); url.searchParams.set(parameter, root.dataset[`${prefix}Sort`]!); history.pushState({}, '', url);
      root.dispatchEvent(new CustomEvent('statistics-url-change'));
      render(overview, true);
    } else {
      const card = button.closest<HTMLElement>('.stats-genre-card')!;
      carousels.get(card)?.scroll(Number(button.dataset[`${prefix}Scroll`]));
    }
  }, { signal: events.signal });
  return { render, stop() { events.abort(); animations.forEach(a => a.cancel()); animations.clear(); carousels.forEach(c => c.stop()); carousels.clear(); } };
}
