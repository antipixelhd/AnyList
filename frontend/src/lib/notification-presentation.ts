import { statuses } from './tracker';

export type NotificationSnapshot = {
  results: any[];
  pending: number;
  outbound: any[];
};

export const isNewSeasonReleaseDate = (event: any) => event.kind === 'new_season_release_date';
export const isNewSeasonRelease = (event: any) => event.kind === 'new_season_release';
export const isSeasonReleaseNotification = (event: any) => isNewSeasonReleaseDate(event) || isNewSeasonRelease(event);

function seasonLabel(event: any) {
  const season = event.payload?.season_number ?? event.season_number;
  return season == null ? '' : `Season ${season}`;
}

function seasonReleaseDate(event: any) {
  const value = event.payload?.release_date;
  if (!value) return '';
  const date = String(value).slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return String(value);
  const parsed = new Date(`${date}T00:00:00Z`);
  return Number.isNaN(parsed.getTime()) ? date : new Intl.DateTimeFormat('en', {
    year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC',
  }).format(parsed);
}

export const eventTitle = (event: any) => {
  const title = event.media?.title || event.payload?.title || 'Connection reconciliation';
  const season = seasonLabel(event);
  return isSeasonReleaseNotification(event) && season ? `${title} : ${season}` : title;
};
export const eventSource = (event: any) => isSeasonReleaseNotification(event) ? undefined
  : event.provider ? `${event.provider} sync` : event.kind?.startsWith('initial_') ? 'First import' : 'Connection sync';
export const eventVariant = (event: any) => event.state === 'pending'
  ? (event.kind?.includes('conflict') || event.kind === 'unmatched_import' ? 'warning' : 'info')
  : event.state === 'corrected' ? 'info' : 'success';
export const eventIcon = (event: any) => event.kind === 'rating_needed' ? 'star'
  : isSeasonReleaseNotification(event) ? 'calendar'
  : event.kind === 'unmatched_import' ? 'search'
  : event.kind === 'connection_failure' ? 'warning'
  : event.kind?.includes('conflict') ? 'warning'
  : event.kind === 'playback_removed' ? 'arrows-rotate'
  : event.state === 'pending' ? 'inbox' : 'check';
export const eventBadge = (event: any) => isSeasonReleaseNotification(event) ? undefined
  : event.kind === 'connection_failure' ? 'Connection issue' : event.state === 'pending' ? 'Needs review' : undefined;
export const eventHref = (event: any) => event.media ? `/title/${event.media.id}` : event.payload?.resolve_url;
export const eventImage = (event: any) => event.payload?.season_poster_path || event.media?.poster || null;
export const eventCanChooseStatus = (event: any) => Boolean(event.media && event.previous_status && event.proposed_status && event.previous_status !== event.proposed_status && !['rating_conflict', 'deletion_conflict'].includes(event.kind));
export const eventCanKeepPrevious = (event: any) => Boolean(event.media && !['rating_conflict', 'deletion_conflict'].includes(event.kind));
export const eventMessage = (event: any) => isNewSeasonReleaseDate(event)
  ? `Release date: ${seasonReleaseDate(event) || 'Date unavailable'}`
  : isNewSeasonRelease(event)
    ? `${seasonLabel(event) || 'New season'} is now releasing`
    : isSeasonReleaseNotification(event) || event.kind === 'rating_needed' || (event.state === 'pending' && (eventCanChooseStatus(event) || event.kind === 'rating_conflict')) ? undefined : event.message;
export const seasonArtworkFallback = (event: any) => Boolean(isSeasonReleaseNotification(event) && event.payload?.season_artwork_fallback);
export const seasonArtworkRetry = (event: any) => Boolean(isSeasonReleaseNotification(event) && event.payload?.artwork_retry);
export const historyChanges = (event: any) => (event.payload?.changes || []).filter((change: any) => change.field !== 'status');
export const historyLabel = (field: string) => ({ start_date: 'Start date', finish_date: 'Finish date', progress: 'Progress' } as Record<string, string>)[field] || field;
export const historyValue = (value: any) => value == null || value === '' ? 'Not set' : String(value);
export const statusLabel = (value: string | null | undefined) => statuses.find(([status]) => status === value)?.[1] || value || 'Unknown';

export const eventKey = (event: any) => `event:${event.id}`;
export const outboundKey = (item: any) => `outbound:${item.media_id ?? item.title ?? 'unknown'}`;

function stableValue(value: any): any {
  if (Array.isArray(value)) return value.map(stableValue);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, stableValue(value[key])]));
  return value;
}

export function notificationVersion(value: any, kind: 'event' | 'outbound') {
  const selected = kind === 'event'
    ? {
        id: value.id, kind: value.kind, state: value.state, provider: value.provider,
        message: value.message, previous_status: value.previous_status, proposed_status: value.proposed_status,
        previous_score: value.previous_score, proposed_score: value.proposed_score, season_number: value.season_number,
        priority: value.priority, dismissible: value.dismissible, payload: value.payload,
        media: value.media && { id: value.media.id, title: value.media.title, type: value.media.type, poster: value.media.poster },
      }
    : {
        media_id: value.media_id, title: value.title, poster: value.poster, state: value.state,
        deliveries: value.deliveries,
      };
  return JSON.stringify(stableValue(selected));
}

export const shouldAutoSee = (event: any) => Boolean(event.dismissible && event.priority === 'low' && event.kind !== 'rating_needed' && !isSeasonReleaseNotification(event));
