import { gameStatuses } from './game-editor-data.ts';
import { mediaDomains } from './media-domains.ts';
import { profileListQuery } from './profile-list-query.ts';
import { statuses } from './tracking-statuses.ts';
import { statisticsSection, statisticsSectionLabel } from './unified-statistics-data.ts';

export function profileLoadingFrame(kind: string, url: URL, nav: HTMLElement) {
  const game = kind === 'games';
  const statistics = kind.startsWith('stats');
  const template = nav.querySelector<HTMLTemplateElement>(statistics
    ? '[data-profile-stats-loading]'
    : `[data-profile-list-loading="${game ? 'game' : 'screen'}"]`);
  const frame = template?.content.firstElementChild?.cloneNode(true) as HTMLElement | undefined;
  if (!frame) return;
  if (!statistics) {
    // Pending controls are inert; track the surrounding frame so an early
    // mouse hover can still reveal count placeholders without enabling them.
    frame.addEventListener('pointermove', event => {
      if (event.pointerType !== 'mouse') return;
      const heading = frame.querySelector<HTMLElement>('.category-heading')?.getBoundingClientRect();
      const counts = frame.querySelector<HTMLElement>('.status-filters')?.getBoundingClientRect();
      frame.toggleAttribute('data-list-counts-hover', !!heading && !!counts && event.clientX >= counts.left &&
        event.clientX <= counts.right && event.clientY >= heading.top && event.clientY <= counts.bottom);
    });
    frame.addEventListener('pointerleave', () => frame.removeAttribute('data-list-counts-hover'));
  }
  if (statistics) {
    const domain = kind === 'stats/games' ? 'game' : 'screen';
    const section = statisticsSection(domain, url.searchParams.get('section') || 'overview');
    const media = url.searchParams.get('media');
    frame.dataset.domain = domain;
    frame.dataset.section = section;
    frame.dataset.media = media === 'movie' || media === 'series' ? media : 'all';
    frame.querySelector<HTMLElement>('[data-loading-stats-heading]')!.textContent = statisticsSectionLabel(section);
    const group = frame.querySelector<HTMLElement>(`[data-statistics-domain="${domain}"]`)!;
    group.hidden = false;
    const selected = group.querySelector<HTMLButtonElement>(`[data-section="${section}"]`);
    selected?.classList.add('is-selected');
    selected?.setAttribute('aria-pressed', 'true');
    return frame;
  }

  const preferences = mediaDomains(JSON.parse(nav.dataset.mediaDomains || '{}'));
  const choices = game ? gameStatuses : statuses;
  const query = profileListQuery(url.searchParams, choices, preferences.default_sort);
  const status = query.get('status') || 'all';
  const combined = kind === 'list';
  const achievements = game && preferences.show_game_achievements;

  const shell = frame.querySelector<HTMLElement>('[data-pending-list-shell]')!;
  if (game) shell.dataset.showAchievements = String(achievements);
  frame.querySelectorAll<HTMLElement>('.entry-head,.entry-row').forEach(row => row.classList.toggle('combined-row', combined));
  frame.querySelectorAll<HTMLElement>('.entry-type,.entry-type-column').forEach(cell => cell.hidden = !combined);
  frame.querySelectorAll<HTMLElement>('.game-list-achievements,.game-achievements-column').forEach(cell => cell.hidden = !achievements);
  frame.querySelectorAll<HTMLButtonElement>('.status-filters [data-status]').forEach(button => {
    const selected = button.dataset.status === status;
    button.classList.toggle('selected', selected);
    button.setAttribute('aria-pressed', String(selected));
  });
  const knownGroup = choices.find(([value]) => value === status)?.[1];
  if (knownGroup) frame.querySelector('[data-pending-group-heading]')!.textContent = knownGroup;

  frame.querySelector<HTMLInputElement>('[name=q]')!.value = query.get('q') || '';
  const year = query.get('year');
  if (year) frame.querySelector('#pending-year-value')!.textContent = year;
  const genre = query.get('genre');
  if (genre) frame.querySelector('#pending-genre-filter-value')!.textContent = genre;
  for (const [name, id] of [['sort', 'sort'], ['favorites', 'favorite']]) {
    const select = frame.querySelector<HTMLSelectElement>(`select[name="${name}"]`)!;
    select.value = query.get(name) || '';
    frame.querySelector(`#pending-${id}-filter-value`)!.textContent =
      select.selectedOptions[0]?.textContent || '';
  }
  return frame;
}
