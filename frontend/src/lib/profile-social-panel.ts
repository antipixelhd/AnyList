import {initializeProfileSections} from './profile-section-selector';
import {panelActive} from './profile-panel';

interface SocialPerson {
  id?: number;
  username: string;
  display_name: string;
  has_avatar?: boolean;
}

export function initializeSocial(root:HTMLElement, signal:AbortSignal) {
  initializeProfileSections();
  const page = root.querySelector<HTMLElement>('[data-social-page]');
  if (!page) return;
  const searchForm = page.querySelector<HTMLFormElement>('[data-social-search-form]');
  const searchInput = searchForm?.querySelector<HTMLInputElement>('input[name=q]');
  let searchRequest = 0;
  let searchController:AbortController|undefined;

  function createCard(person: SocialPerson) {
    const name = person.display_name || person.username;
    const card = document.createElement('article');
    card.className = 'social-person-card';
    card.dataset.socialPerson = person.username;
    const link = document.createElement('a');
    link.className = 'social-person-link';
    link.href = `/user/${encodeURIComponent(person.username)}`;
    link.setAttribute('aria-label', `View ${name}'s profile`);
    link.title = name;
    if (person.has_avatar && person.id) {
      const image = document.createElement('img');
      image.src = `/api/proxy/profile/avatar/${person.id}`;
      image.alt = '';
      image.loading = 'lazy';
      link.append(image);
    } else {
      const placeholder = document.createElement('span');
      placeholder.className = 'social-person-placeholder';
      placeholder.setAttribute('aria-hidden', 'true');
      placeholder.textContent = name.slice(0, 1).toUpperCase();
      link.append(placeholder);
    }
    const label = document.createElement('span');
    label.className = 'social-person-name';
    label.textContent = name;
    link.append(label);
    card.append(link);
    return card;
  }

  const renderGroup = (group: 'following' | 'followers' | 'search', people: SocialPerson[], emptyText: string) => {
    const grid = page.querySelector<HTMLElement>(`[data-social-grid="${group}"]`);
    const empty = page.querySelector<HTMLElement>(`[data-social-empty="${group}"]`);
    if (!grid || !empty) return;
    grid.replaceChildren(...people.map(createCard));
    empty.textContent = emptyText;
    empty.hidden = people.length > 0;
  }

  const searchPeople = async () => {
    const q = searchInput?.value.trim() || '';
    searchController?.abort();
    const request = ++searchRequest;
    const empty = page.querySelector<HTMLElement>('[data-social-empty="search"]');
    if (!q) {
      renderGroup('search', [], 'Search for public profiles by name.');
      return;
    }
    if (empty) { empty.textContent = 'Searching public profiles…'; empty.hidden = false; }
    const controller=searchController=new AbortController();
    const searchSignal=AbortSignal.any([signal,controller.signal]);
    try {
      const response = await fetch(`/api/proxy/tracking/people-search?q=${encodeURIComponent(q)}`, {signal:searchSignal});
      if (!response.ok) throw new Error('Could not search public profiles.');
      const data = await response.json();
      if (searchSignal.aborted||request !== searchRequest) return;
      renderGroup('search', data.results || [], 'No public profiles match that search.');
    } catch (cause) {
      if (searchSignal.aborted || request !== searchRequest) return;
      if (empty) { empty.textContent = cause instanceof Error ? cause.message : 'Could not search public profiles.'; empty.hidden = false; }
    } finally {
      if(searchController===controller)searchController=undefined;
    }
  }

  page.addEventListener('profile-section-change', event => {
    const section = (event as CustomEvent<{value: string}>).detail.value;
    page.querySelectorAll<HTMLElement>('[data-social-panel]').forEach(panel => {
      panel.hidden = panel.dataset.socialPanel !== section;
    });
    if (section === 'search') searchInput?.focus();
  }, {signal});

  searchForm?.addEventListener('submit', event => {
    event.preventDefault();
    void searchPeople();
  }, {signal});

  let restartSearch=!!searchInput?.value.trim();
  root.addEventListener('anylist:panel-inactive',()=>{
    if(searchController){restartSearch=true;searchController.abort();}
  },{signal});
  root.addEventListener('anylist:panel-active',()=>{
    if(restartSearch){restartSearch=false;void searchPeople();}
  },{signal});
  if(restartSearch&&panelActive(root)){restartSearch=false;void searchPeople();}
}
