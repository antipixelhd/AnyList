import { responsiveArtwork } from "./responsive-artwork";

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
  const image = responsiveArtwork(path, { size, route: 'direct' });
  if (!image) return '';
  try {
    const url = new URL(image.src);
    return url.protocol === 'https:' ? url.href : '';
  } catch { return ''; }
}

export const scoreLabel = (value: number | null) => value == null || value === 0 ? '—' : Number(value.toFixed(1)).toString();

function firstRating(details: any) {
  // Earlier activity payloads did not distinguish a first rating from an edit.
  return details.rating_first === true || (details.rating_first == null && details.previous_score == null);
}

export function activityRating(activity: any) {
  const details = activity.payload || {};
  if (!details.rating_changed) return null;
  const previous = typeof details.previous_score === 'number' ? details.previous_score : null;
  const current = typeof activity.score === 'number' ? activity.score : null;
  const label = previous == null
    ? `Rated ${scoreLabel(current)} out of 10`
    : `Rating changed from ${scoreLabel(previous)} to ${scoreLabel(current)} out of 10`;
  return { previous, current, label };
}

export function activityAction(activity: any) {
  const details = activity.payload || {};
  const finished = details.finished_seasons || [];
  if (details.status_changed && ['completed', 'dropped', 'paused', 'planning'].includes(activity.status)) {
    return activity.status === 'planning' ? 'Plans to watch' : statuses.find(([status]) => status === activity.status)?.[1] || 'Updated';
  }
  if (activity.media?.type === 'series' && details.episodes_watched && details.position_start && details.position) {
    const first = /^S(\d+)E(\d+)$/.exec(details.position_start);
    const last = /^S(\d+)E(\d+)$/.exec(details.position);
    if (first && last) {
      if (first[1] === last[1]) {
        return `Watched Season ${first[1]} Episode ${first[2]}${first[2] === last[2] ? '' : `–${last[2]}`} of`;
      }
      return `Watched Season ${first[1]} Episode ${first[2]} through Season ${last[1]} Episode ${last[2]} of`;
    }
  }
  if (activity.media?.type === 'series' && finished.length) {
    const seasons = finished.join(finished.length > 1 ? ' and ' : '');
    return `Watched ${finished.length === 1 ? 'season' : 'seasons'} ${seasons} of`;
  }
  if (activity.media?.type === 'series' && details.episodes_watched) {
    const count = Number(details.episodes_watched);
    const end = Number(details.progress);
    if (Number.isFinite(end) && end > 0) {
      const start = Math.max(1, end - count + 1);
      return count === 1 ? `Watched episode ${end} of` : `Watched episodes ${start}–${end} of`;
    }
    return `Watched ${count === 1 ? 'an episode' : `${count} episodes`} of`;
  }
  if (details.status_changed && activity.status === 'watching' && details.started_watching === true) return 'Started Watching';
  if (details.rating_changed) return firstRating(details) ? 'Rated' : 'Changed Rating for';
  return '';
}

export const activityLabel = activityAction;

export function activityTime(value: string, now = Date.now()) {
  const normalized = /(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : `${value}Z`;
  const date = new Date(normalized);
  const elapsed = Math.max(0, now - date.getTime());
  const minutes = Math.floor(elapsed / 60_000);
  const hours = Math.floor(elapsed / 3_600_000);
  const days = Math.floor(elapsed / 86_400_000);
  let label = 'Just now';
  if (minutes >= 1 && minutes < 60) label = `${minutes} ${minutes === 1 ? 'minute' : 'minutes'} ago`;
  else if (hours >= 1 && hours < 24) label = `${hours} ${hours === 1 ? 'hour' : 'hours'} ago`;
  else if (days >= 1 && days < 7) label = `${days} ${days === 1 ? 'day' : 'days'} ago`;
  else if (days >= 7 && days < 30) {
    const weeks = Math.floor(days / 7);
    label = `${weeks} ${weeks === 1 ? 'week' : 'weeks'} ago`;
  } else if (days >= 30 && days < 365) {
    const months = Math.floor(days / 30);
    label = `${months} ${months === 1 ? 'month' : 'months'} ago`;
  } else if (days >= 365) {
    const years = Math.floor(days / 365);
    label = `${years} ${years === 1 ? 'year' : 'years'} ago`;
  }
  return {
    datetime: normalized,
    label,
    exact: `${date.toLocaleString('en-GB', {dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC'})} UTC`,
  };
}
