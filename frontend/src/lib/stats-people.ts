import type { ActorGroup, ActorTitle, StaffGroup, StaffTitle, Overview } from './stats-overview-data';
import { displayNumber } from './stats-overview-data';
import { genreSort, genreTime } from './stats-genres-data';
import { actorArtwork, actorTitleImage, hasCharacterImages, rankedActors } from './stats-actors-data';
import { personTitleLabel, rankedStaff } from './stats-staff-data';
import { carousel } from './stats-carousel';
import { responsiveArtwork } from './responsive-artwork';

export function mountPeople(root: HTMLElement, section: 'actors' | 'staff') {
  const prefix = section === 'staff' ? 'staff' : 'actor';
  const titleImage = (title: ActorTitle | StaffTitle, mode: 'media' | 'characters') => 'roles' in title ? title.poster : actorTitleImage(title, mode);
  const grid = root.querySelector<HTMLElement>(`[data-${section}-grid]`)!;
  const template = root.querySelector<HTMLTemplateElement>(`[data-${prefix}-card-template]`)!;
  const posterTemplate = root.querySelector<HTMLTemplateElement>('[data-genre-poster-template]')!;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)'), events = new AbortController();
  const carousels = new Map<HTMLElement, ReturnType<typeof carousel>>(), animations = new Map<HTMLElement, Animation>();
  let overview: Overview | null = null;
  const query = <T extends HTMLElement = HTMLElement>(card: HTMLElement, selector: string) => card.querySelector<T>(selector)!;
  function setArtwork(link: HTMLElement, url: string | null, label: string, portrait = false) {
    const art = responsiveArtwork(url, { size: 'w185', sizes: portrait ? '(max-width:650px) 72px, 110px' : '(max-width:650px) 15vw, 70px', route: 'direct' });
    let img = link.querySelector('img'), placeholder = link.querySelector<HTMLElement>(portrait ? '.stats-actor-placeholder' : '.stats-genre-placeholder');
    if (art) {
      if (!img) { img = document.createElement('img'); img.alt = ''; img.loading = 'lazy'; img.draggable = false; link.prepend(img); }
      const changed = img.getAttribute('src') !== art.src;
      img.src = art.src;
      if (changed && !portrait && !reduced.matches) img.animate([{ opacity: .4 }, { opacity: 1 }], { duration: 240, easing: 'ease-out' });
      if (art.srcset) img.srcset = art.srcset; else img.removeAttribute('srcset');
      if (art.sizes) img.sizes = art.sizes; else img.removeAttribute('sizes');
      placeholder?.remove();
    } else {
      img?.remove();
      if (!placeholder) { placeholder = document.createElement('span'); placeholder.className = portrait ? 'stats-actor-placeholder' : 'stats-genre-placeholder'; placeholder.setAttribute('aria-hidden', 'true'); link.prepend(placeholder); }
      placeholder.textContent = label.slice(0, 1);
    }
  }
  function createCard(row: ActorGroup | StaffGroup) {
    const card = template.content.firstElementChild!.cloneNode(true) as HTMLElement;
    card.dataset[`${prefix}Key`] = row.key; card.dataset[`${prefix}Signature`] = JSON.stringify(row); card.setAttribute('aria-label', row.label);
    const link = query<HTMLAnchorElement>(card, `[data-${prefix}-link]`); link.textContent = row.label; link.href = row.href; link.title = row.label;
    const portrait = query<HTMLAnchorElement>(card, `[data-${prefix}-portrait]`); portrait.href = row.href; portrait.setAttribute('aria-label', row.label);
    setArtwork(portrait, row.image, row.label, true);
    query(card, `[data-${prefix}-count]`).textContent = displayNumber(row.titles);
    query(card, `[data-${prefix}-score]`).textContent = displayNumber(row.mean_score, 2);
    query(card, `[data-${prefix}-time]`).textContent = genreTime(row);
    const track = query(card, '[data-genre-track]'); track.setAttribute('aria-label', `Highest-rated ${row.label} titles`);
    track.replaceChildren(...row.top_titles.map(title => {
      const link = posterTemplate.content.firstElementChild!.cloneNode(true) as HTMLAnchorElement;
      link.dataset[`${prefix}TitleKey`] = title.key; link.href = title.href; link.setAttribute('aria-label', personTitleLabel(title));
      setArtwork(link, titleImage(title, actorArtwork(root.dataset[`${prefix}Artwork`])), title.title);
      query(link, '.stats-genre-tooltip').textContent = personTitleLabel(title);
      return link;
    }));
    query(card, `[data-${prefix}-scroll="-1"]`).setAttribute('aria-label', `Previous ${row.label} titles`);
    query(card, `[data-${prefix}-scroll="1"]`).setAttribute('aria-label', `Next ${row.label} titles`);
    return card;
  }
  function render(next: Overview | null, animate = false) {
    overview = next;
    const before = new Map([...grid.children].map(card => [card, card.getBoundingClientRect()]));
    animations.forEach(a => a.cancel()); animations.clear();
    const existing = new Map([...grid.querySelectorAll<HTMLElement>(`[data-${prefix}-key]`)].map(card => [card.dataset[`${prefix}Key`], card]));
    const rows = section === 'staff' ? rankedStaff(next?.staff || [], genreSort(root.dataset.staffSort)) : rankedActors(next?.actors || [], genreSort(root.dataset.actorSort));
    const available = hasCharacterImages(next?.actors || []);
    const mode = actorArtwork(root.dataset[`${prefix}Artwork`]);
    const cards = rows.map((row, index) => {
      let card = existing.get(row.key);
      if (!card || card.dataset[`${prefix}Signature`] !== JSON.stringify(row)) card = createCard(row);
      const rank = query(card, `[data-${prefix}-rank]`); rank.textContent = String(index + 1); rank.setAttribute('aria-label', `Rank ${index + 1}`);
      row.top_titles.forEach((title, i) => setArtwork(query(card!, '[data-genre-track]').children[i] as HTMLElement, titleImage(title, mode), title.title));
      return card;
    });
    for (const [card, controller] of carousels) if (!cards.includes(card)) { controller.stop(); carousels.delete(card); }
    grid.replaceChildren(...cards);
    cards.forEach(card => {
      if (!carousels.has(card)) carousels.set(card, carousel(query(card, '[data-genre-track]'), reduced)); else carousels.get(card)!.bounds();
      if (animate && !reduced.matches && root.dataset.section === section) {
        const old = before.get(card), now = card.getBoundingClientRect();
        const frames = old ? [{ transform: `translate(${old.left - now.left}px,${old.top - now.top}px)` }, { transform: 'translate(0,0)' }] : [{ opacity: 0 }, { opacity: 1 }];
        const animation = card.animate(frames, { duration: 600, easing: 'cubic-bezier(.22,.61,.36,1)' }); animations.set(card, animation);
        animation.onfinish = () => animations.delete(card);
      }
    });
    root.querySelector<HTMLElement>(`[data-${section}-empty]`)!.hidden = rows.length > 0;
    root.querySelectorAll<HTMLButtonElement>(`button[data-${prefix}-sort]`).forEach(b => b.setAttribute('aria-pressed', String(b.dataset[`${prefix}Sort`] === root.dataset[`${prefix}Sort`])));
    root.querySelectorAll<HTMLButtonElement>(`button[data-${prefix}-artwork]`).forEach(b => {
      b.disabled = b.dataset[`${prefix}Artwork`] === 'characters' && !available;
      b.setAttribute('aria-pressed', String(b.dataset[`${prefix}Artwork`] === mode));
    });
  }
  root.addEventListener('click', event => {
    const button = (event.target as Element).closest<HTMLButtonElement>(`button[data-${prefix}-sort],button[data-${prefix}-artwork],[data-${prefix}-scroll]`);
    if (!button) return;
    if (button.dataset[`${prefix}Sort`] || button.dataset[`${prefix}Artwork`]) {
      const url = new URL(location.href);
      if (button.dataset[`${prefix}Sort`]) { root.dataset[`${prefix}Sort`] = genreSort(button.dataset[`${prefix}Sort`]); url.searchParams.set(`${prefix}_sort`, root.dataset[`${prefix}Sort`]!); }
      if (button.dataset[`${prefix}Artwork`]) { root.dataset[`${prefix}Artwork`] = actorArtwork(button.dataset[`${prefix}Artwork`]); url.searchParams.set('artwork', root.dataset[`${prefix}Artwork`]!); }
      history.pushState({}, '', url); root.dispatchEvent(new CustomEvent('statistics-url-change')); render(overview, !!button.dataset[`${prefix}Sort`]);
    } else carousels.get(button.closest<HTMLElement>('.stats-actor-card')!)?.scroll(Number(button.dataset[`${prefix}Scroll`]));
  }, { signal: events.signal });
  return { render, stop() { events.abort(); animations.forEach(a => a.cancel()); carousels.forEach(c => c.stop()); carousels.clear(); } };
}
