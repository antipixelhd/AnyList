import type { ProfileStats } from './profile-stats-data';

export type MediaScope = 'all' | 'movie' | 'series';
export type ChartMetric = 'titles' | 'hours' | 'mean_score';
export interface MetricGroup {
  key: string;
  label: string;
  titles: number;
  minutes: number;
  mean_score: number | null;
  rated_titles: number;
}
export interface Distribution { key: string; label: string; titles: number; }
export interface GenreTitle { key: string; title: string; poster: string | null; href: string; score: number; }
export interface MediaGroup extends MetricGroup {
  top_titles: GenreTitle[];
  runtime_missing_plays: number;
}
export interface GenreGroup extends MediaGroup {
  browse_filters: { movie: string; series: string };
}
export interface StudioGroup extends MediaGroup { href: string; }
export interface ActorTitle extends Omit<GenreTitle, 'score'> { score: number | null; character: string | null; character_image: string | null; }
export interface ActorGroup extends MetricGroup {
  image: string | null; href: string; top_titles: ActorTitle[]; runtime_missing_plays: number;
}
export interface Overview {
  media_type: MediaScope;
  totals: {
    listed_titles: number; watched_titles: number; episode_plays: number; distinct_episodes: number;
    watch_minutes: number; watch_days: number; planned_minutes: number; planned_days: number;
    mean_score: number | null; standard_deviation: number | null; rated_titles: number;
  };
  scores: MetricGroup[];
  episode_counts: MetricGroup[];
  statuses: Distribution[];
  formats: Distribution[];
  countries: (Distribution & { share: number })[];
  release_years: MetricGroup[];
  watch_years: MetricGroup[];
  coverage: Record<string, number>;
  genres?: GenreGroup[];
  actors?: ActorGroup[];
  studios?: StudioGroup[];
}
export interface OverviewResponse {
  profile: ProfileStats['profile'];
  owner: boolean; following: boolean; follows_you: boolean; combine_lists: boolean;
  status: 'ready' | 'pending' | 'error';
  generation: number | null;
  computed_at: string | null;
  next_update_at: string;
  refreshing: boolean;
  overview: Overview | null;
}

export const metricLabels: Record<ChartMetric, string> = { titles: 'Titles watched', hours: 'Hours watched', mean_score: 'Mean score' };
export const statusLabels: Record<string, string> = { watching: 'Watching', completed: 'Completed', paused: 'Paused', dropped: 'Dropped', planning: 'Planning' };
export function countryLabel(code: string): string {
  return code === 'Unknown' ? 'Unknown' : new Intl.DisplayNames(['en'], { type: 'region' }).of(code) || code;
}
export function metricValue(row: MetricGroup, metric: ChartMetric): number | null {
  return metric === 'hours' ? row.minutes / 60 : row[metric];
}
export function displayNumber(value: number | null, decimals = 0): string {
  return value === null ? '—' : value.toLocaleString('en', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}
export function headlineValues(data: Overview): { label: string; value: string; detail: string }[] {
  const { totals: t, coverage: c } = data;
  const watchUnavailable = t.watched_titles > 0 && !(c.runtime_known_plays + c.runtime_estimated_plays);
  const plannedUnknown = c.planned_unknown_titles > 0;
  const plannedValue = plannedUnknown && !t.planned_minutes ? '—' : `${plannedUnknown ? '≥ ' : ''}${displayNumber(t.planned_days, 2)}`;
  return [
    { label: 'Total titles', value: displayNumber(t.listed_titles), detail: 'Current movie and series list entries' },
    { label: data.media_type === 'movie' ? 'Movies watched' : 'Episodes watched', value: displayNumber(data.media_type === 'movie' ? t.watched_titles : t.episode_plays), detail: data.media_type === 'movie' ? 'Distinct movies with completed watch evidence' : `${displayNumber(t.distinct_episodes)} distinct episodes; includes recorded repeats and specials` },
    { label: 'Days watched', value: watchUnavailable ? '—' : `${c.runtime_missing_plays > 0 ? '≥ ' : ''}${displayNumber(t.watch_days, 2)}`, detail: `${displayNumber(t.watch_minutes / 60, 1)} estimated hours; missing runtimes are excluded` },
    { label: 'Days planned', value: plannedValue, detail: 'Remaining released regular workload in Planning; missing catalogue/runtime is unavailable' },
    { label: 'Mean score', value: displayNumber(t.mean_score, 2), detail: `${displayNumber(t.rated_titles)} rated watched titles, on a 0–10 scale` },
    { label: 'Standard deviation', value: displayNumber(t.standard_deviation, 2), detail: 'Population deviation of current scores, once per watched title' },
  ];
}
export function distributionRows(data: Overview, kind: 'statuses' | 'formats' | 'countries') {
  const rows = knownRows<Distribution & { share?: number }>(data[kind]);
  const denominator = rows.reduce((sum, row) => sum + (row.share ?? row.titles), 0);
  return rows.map(row => ({ ...row, label: kind === 'countries' ? countryLabel(row.key) : kind === 'statuses' ? statusLabels[row.key] || row.label : row.label,
    percent: denominator ? (row.share ?? row.titles) / denominator * 100 : 0 }));
}
/** Incomplete metadata remains in the payload, outside the visible breakdown. */
export function knownRows<T extends { key: string }>(rows: T[]): T[] {
  return rows.filter(row => row.key !== 'Unknown');
}
/** Range selection belongs only to Watch Year; lifetime graphs never change. */
export function watchYearRows(rows: MetricGroup[], from: string, through: string): MetricGroup[] {
  return rows.filter(row => (!from || Number(row.key) >= Number(from)) && (!through || Number(row.key) <= Number(through)));
}
