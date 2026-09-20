export async function tracker(path: string, token?: string) {
  const port = import.meta.env.BACKEND_PORT ?? '7331';
  const response = await fetch(`http://localhost:${port}/tracking/${path}`, {
    headers: token ? {Authorization: `Bearer ${token}`} : {},
  });
  if (!response.ok) throw new Error(response.status === 403 ? 'This profile is private.' : response.status === 404 ? 'Not found.' : 'Unable to load this page. Please try again.');
  return response.json();
}

export const statuses = [
  ['watching', 'Watching'], ['completed', 'Completed'], ['paused', 'Paused'],
  ['dropped', 'Dropped'], ['planning', 'Plan to Watch'],
];

export function artwork(path: string | null, size = 'w342') {
  if (!path) return '';
  if (path.startsWith('/') && !path.startsWith('//')) return `https://image.tmdb.org/t/p/${size}${path}`;
  try {
    const url = new URL(path);
    return url.protocol === 'https:' ? url.href : '';
  } catch { return ''; }
}

export const scoreLabel = (value: number | null) => value == null || value === 0 ? '—' : Number(value.toFixed(1)).toString();

export function activityLabel(activity: any) {
  const details = activity.payload || {};
  const finished = details.finished_seasons || [];
  if (finished.length) return `Finished ${finished.map((season: number) => `Season ${season}`).join(', ')}`;
  if (details.episodes_watched) return `Watched ${details.episodes_watched} ${details.episodes_watched === 1 ? 'episode' : 'episodes'}${details.position ? ` · ${details.position}` : ''}`;
  if (details.status_changed) return activity.status === 'completed' ? 'Completed' : activity.status === 'planning' ? 'Plans to watch' : statuses.find(([status]) => status === activity.status)?.[1] || 'Updated';
  if (details.rating_changed) return 'Rated';
  return activity.status === 'completed' ? 'Completed' : activity.status === 'planning' ? 'Plans to watch' : statuses.find(([status]) => status === activity.status)?.[1] || 'Updated';
}

export function activityTime(value: string) {
  const normalized = /(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : `${value}Z`;
  return {
    datetime: normalized,
    label: `${new Date(normalized).toLocaleString('en-GB', {dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC'})} UTC`,
  };
}
