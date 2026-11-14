import {categoryChoices, categoryName, defaultGameCategories, type CategoryPresentation} from './list-categories.ts';

let scope = '';
let saved: CategoryPresentation | undefined;

export function viewerGameCategories(): CategoryPresentation {
  if (typeof document === 'undefined' || !document.body?.dataset || !document.querySelector) return defaultGameCategories();
  const viewer = document.body.dataset.cacheUser || 'anonymous';
  if (scope !== viewer) {scope = viewer; saved = undefined;}
  const seed = document.querySelector('[data-viewer-game-categories]');
  if (seed) {
    const value = JSON.parse(seed.textContent || 'null') as CategoryPresentation | null;
    if (value?.categories.length && (!saved || value.revision > saved.revision)) saved = value;
  }
  return saved || defaultGameCategories();
}

export function acceptGameCategories(presentation: CategoryPresentation) {
  viewerGameCategories();
  if (saved && presentation.revision <= saved.revision) return;
  saved = presentation;
  document.dispatchEvent(new CustomEvent('anylist:game-categories', {detail: presentation}));
  document.dispatchEvent(new Event('anylist:cache-invalidated'));
}

export const gameCategoryChoices = (current?: string) => categoryChoices(viewerGameCategories(), current);
export const gameCategoryName = (id: string) => categoryName(viewerGameCategories(), id);

export async function refreshGameCategories(signal?: AbortSignal) {
  const response = await fetch('/api/proxy/tracking/list-categories/game', {signal, cache: 'no-store'});
  if (response.ok) acceptGameCategories(await response.json());
}

export function showCategoryInfo(button: HTMLElement, presentation = viewerGameCategories()) {
  document.querySelector('[data-category-info-popover]')?.remove();
  const popover = document.createElement('div');
  popover.dataset.categoryInfoPopover = '';
  popover.className = 'category-info';
  popover.setAttribute('popover', 'auto');
  const heading = document.createElement('h3');
  heading.textContent = 'Game categories';
  popover.append(heading);
  for (const category of presentation.categories) {
    const name = document.createElement('strong');
    name.textContent = category.label === category.default_label ? category.label : `${category.label} (${category.default_label})`;
    const description = document.createElement('p');
    description.textContent = category.description;
    popover.append(name, description);
  }
  (button.closest('dialog') || document.body).append(popover);
  popover.showPopover();
  popover.addEventListener('toggle', event => {if ((event as ToggleEvent).newState === 'closed') popover.remove();});
}
