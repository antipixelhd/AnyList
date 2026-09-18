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
