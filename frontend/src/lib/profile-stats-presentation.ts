import type { ProfileStats } from './profile-stats-data';

export const statsStatuses = [
  { key: 'completed', label: 'Completed' },
  { key: 'watching', label: 'Watching' },
  { key: 'planning', label: 'Plan to watch' },
  { key: 'paused', label: 'Paused' },
  { key: 'dropped', label: 'Dropped' },
] as const;

export function watchTime(minutes: number) {
  const total = Math.max(0, Math.round(minutes));
  const days = Math.floor(total / 1440);
  const hours = Math.floor(total / 60) % 24;
  const remaining = total % 60;
  if (days) return { value: days, unit: days === 1 ? 'day' : 'days', detail: `${hours}h ${remaining}m` };
  if (hours) return { value: hours, unit: hours === 1 ? 'hour' : 'hours', detail: `${remaining}m` };
  return { value: remaining, unit: remaining === 1 ? 'minute' : 'minutes', detail: '' };
}

/** Zero months must remain visible instead of joining separated bursts of activity. */
export function activityTimeline(activity: ProfileStats['activity'], year: number | null) {
  const sorted = [...activity].sort((a, b) => a.month.localeCompare(b.month));
  if (!sorted.length && year === null) return [];
  const start = year === null ? sorted[0].month : `${year}-01`;
  const end = year === null ? sorted[sorted.length - 1].month : `${year}-12`;
  const byMonth = new Map(activity.map(item => [item.month, item]));
  const [firstYear, firstMonth] = start.split('-').map(Number);
  const [lastYear, lastMonth] = end.split('-').map(Number);
  const months: ProfileStats['activity'] = [];
  for (let index = firstYear * 12 + firstMonth - 1; index <= lastYear * 12 + lastMonth - 1; index++) {
    const month = `${Math.floor(index / 12)}-${String(index % 12 + 1).padStart(2, '0')}`;
    months.push(byMonth.get(month) ?? { month, movies: 0, episodes: 0 });
  }
  return months;
}

/** Include half-point ratings and season averages in their exact one-point range. */
export function scoreBuckets(distribution: ProfileStats['scores']['distribution']) {
  return Array.from({ length: 10 }, (_, index) => {
    const score = index + 1;
    return {
      score,
      range: `Above ${score - 1}, up to ${score}`,
      count: distribution.filter(item => item.score > score - 1 && item.score <= score)
        .reduce((total, item) => total + item.count, 0),
    };
  });
}

export function statsHighlights(data: ProfileStats) {
  const series = Math.max(0, data.viewing.unique_titles - data.viewing.unique_movies);
  return {
    titles: data.viewing.unique_titles.toLocaleString(),
    movies: `${data.viewing.unique_movies.toLocaleString()} ${data.viewing.unique_movies === 1 ? 'movie' : 'movies'}`,
    series: `${series.toLocaleString()} series`,
    episodes: data.viewing.unique_episodes.toLocaleString(),
    seasons: data.viewing.unique_seasons.toLocaleString(),
    repeats: data.viewing.repeat_views.toLocaleString(),
    average: data.scores.average === null ? '—' : data.scores.average.toFixed(1),
    rated: `${data.scores.rated.toLocaleString()} ${data.scores.rated === 1 ? 'rated title' : 'rated titles'}`,
    current: data.current.total.toLocaleString(),
    completion: data.current.total ? Math.round((data.current.statuses.completed || 0) / data.current.total * 100) : 0,
  };
}
