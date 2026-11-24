interface EpisodeCredits {
  episodes: {season: number; episode: number; title: string; roles: string[]}[];
  next_season: number | null;
}

export function mountContributorEpisodeCredits(region: HTMLElement, personKey: string, signal: AbortSignal) {
  const requests = new Map<HTMLDetailsElement, AbortController>();
  const cancel = () => {
    for (const [details, request] of requests) {
      request.abort();
      if (!details.hasAttribute('data-loaded')) details.open = false;
    }
  };
  signal.addEventListener('abort', cancel, {once: true});

  async function load(details: HTMLDetailsElement) {
    if (signal.aborted || !details.open || requests.has(details) || !details.dataset.series) return;
    const request = new AbortController();
    requests.set(details, request);
    const list = details.querySelector<HTMLUListElement>('[data-contributor-episode-list]')!;
    const status = details.querySelector<HTMLElement>('[data-contributor-episode-status]')!;
    const more = details.querySelector<HTMLButtonElement>('[data-contributor-episodes-more]')!;
    const path = new URL(`/api/proxy/tracking/contributors/person/${encodeURIComponent(personKey)}/series/${details.dataset.series}/episode-credits`, location.origin);
    if (details.dataset.nextSeason) path.searchParams.set('after_season', details.dataset.nextSeason);
    more.disabled = true;
    status.textContent = 'Loading episode credits…';
    try {
      const response = await fetch(path, {signal: request.signal, cache: 'no-store'});
      if (!response.ok) throw new Error('Episode credits are unavailable.');
      const result: EpisodeCredits = await response.json();
      if (request.signal.aborted) return;
      for (const row of result.episodes) {
        const item = document.createElement('li');
        const season = String(row.season).padStart(2, '0');
        const episode = String(row.episode).padStart(2, '0');
        item.textContent = [`S${season}E${episode}`, row.title, row.roles.join(' / ')].filter(Boolean).join(' · ');
        list.append(item);
      }
      details.setAttribute('data-loaded', '');
      details.dataset.nextSeason = result.next_season === null ? '' : String(result.next_season);
      more.hidden = result.next_season === null;
      more.textContent = 'More seasons';
      status.textContent = list.childElementCount ? '' : more.hidden ?
        'No episode-level credits found.' : 'No episode-level credits in these seasons.';
      if (more.hidden && document.activeElement === more) details.querySelector('summary')?.focus();
    } catch {
      if (request.signal.aborted) return;
      more.hidden = false;
      more.textContent = 'Try again';
      status.textContent = 'Episode credits are unavailable.';
    } finally {
      requests.delete(details);
      more.disabled = false;
      if (request.signal.aborted) status.textContent = '';
    }
  }

  region.addEventListener('toggle', event => {
    const details = event.target;
    if (!(details instanceof HTMLDetailsElement) || !details.matches('[data-contributor-episodes]')) return;
    if (!details.open) requests.get(details)?.abort();
    else if (!details.hasAttribute('data-loaded')) void load(details);
  }, {capture: true, signal});
  region.addEventListener('click', event => {
    const target = (event.target as Element).closest('[data-contributor-episodes-more]');
    const details = target?.closest<HTMLDetailsElement>('[data-contributor-episodes]');
    if (details) void load(details);
  }, {signal});
  return cancel;
}
