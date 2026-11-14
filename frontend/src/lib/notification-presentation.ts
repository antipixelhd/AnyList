import { statuses } from './tracker';
import { formatDate } from './date-time';

export type NotificationSnapshot = {
  results: any[];
  pending: number;
  outbound: any[];
};

export const isNewSeasonReleaseDate = (event: any) => event.kind === 'new_season_release_date';
export const isNewSeasonRelease = (event: any) => event.kind === 'new_season_release';
export const isSeasonReleaseNotification = (event: any) => isNewSeasonReleaseDate(event) || isNewSeasonRelease(event);
export const isSeasonRating = (event:any) => event.kind==='rating_needed' && Number(event.season_number)>0;
// Keep each request independently actionable while presenting a final season
// and its series as one card. Other seasons retain their own cards.
export function groupRatingNotifications(events:any[]) {
  const pairs=new Map<any,any>();
  const grouped=new Set<any>();
  for(const series of events.filter(event=>event.kind==='rating_needed' && event.media?.type==='series' && !isSeasonRating(event))) {
    const season=events.filter(event=>isSeasonRating(event) && event.media?.id===series.media.id)
      .sort((a,b)=>String(b.created_at||'').localeCompare(String(a.created_at||'')) || b.season_number-a.season_number)[0];
    if(season){pairs.set(season,series);grouped.add(series);}
  }
  return events.filter(event=>!grouped.has(event)).map(event=>pairs.has(event)?{...event,series_rating_request:pairs.get(event)}:event);
}
export const ratingTargets = (event:any) => [event,...(event.series_rating_request?[event.series_rating_request]:[])];
export const ratingActionLabel = (event:any) => isSeasonRating(event)?'Rate season':event.media?.type==='series'?'Rate series':'Rate now';
export const ratingPopupEnabled = (event:any, preferences:any) => isSeasonRating(event)
  ? preferences.show_season_rating_popup!==false : preferences.show_new_ratings_popup!==false;
export const ratingMetadata = (event:any) => {
  if(event.kind!=='rating_needed')return '';
  const media=event.media||{},data=event.payload?.rating_metadata||{};
  const facts:string[]=[];
  const count=(value:any,singular:string)=>Number(value)>0?`${Number(value)} ${singular}${Number(value)===1?'':'s'}`:'';
  if(media.type==='movie') {
    const minutes=Number(data.runtime_minutes??media.runtime);
    if(minutes>0)facts.push([Math.floor(minutes/60)?`${Math.floor(minutes/60)}h`:'',minutes%60?`${minutes%60}m`:''].filter(Boolean).join(' '));
  } else {
    if(!isSeasonRating(event))facts.push(count(data.season_count??media.season_count,'season'));
    facts.push(count(data.episode_count??(isSeasonRating(event)?undefined:media.episode_count),'episode'));
  }
  const year=String(data.year??(isSeasonRating(event)?'':media.year)??'');
  if(/^\d{4}$/.test(year))facts.push(year);
  return facts.filter(Boolean).join(' · ');
};

function seasonLabel(event: any) {
  const season = event.payload?.season_number ?? event.season_number;
  return season == null ? '' : `Season ${season}`;
}

function seasonReleaseDate(event: any) {
  const value = event.payload?.release_date;
  if (!value) return '';
  return formatDate(String(value).slice(0, 10));
}

export const eventTitle = (event: any) => {
  const title = event.media?.title || event.payload?.title || 'Connection reconciliation';
  const season = seasonLabel(event);
  return (isSeasonReleaseNotification(event) || isSeasonRating(event)) && season ? `${title} : ${season}` : title;
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
export const eventCanKeepPrevious = (event: any) => event.kind === 'resume_conflict' || Boolean(event.media && !['rating_conflict', 'deletion_conflict'].includes(event.kind));
export const eventConfirmLabel = (event: any) => event.kind === 'resume_conflict' ? 'Use incoming'
  : event.kind === 'deletion_conflict' ? 'Retry deletion' : event.kind?.startsWith('initial_') ? 'Approve import'
  : event.kind === 'rating_conflict' ? 'Use provider score' : 'Confirm';
export const eventKeepLabel = (event: any) => event.kind === 'resume_conflict' ? 'Keep current' : 'Keep previous';

export function resumeChoices(event: any) {
  if (event.kind !== 'resume_conflict') return [];
  const { incoming, remote, destination } = event.payload || {};
  if (!incoming || !remote) return [];
  return [
    { name: `${incoming.name || 'AnyList'} · Incoming`, time: incoming.timestamp, row: incoming },
    { name: `${destination || 'Nuvio'} · Current`, time: remote.updated_at || remote.last_watched, row: remote },
  ].map(({ name, time, row }) => {
    const seconds = typeof row.position === 'number' ? Math.floor(row.position / 1000) : null;
    const position = seconds == null ? '' : `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
    const episode = row.season != null && row.episode != null ? `S${row.season}E${row.episode}` : '';
    return { name, time, position: [episode, position].filter(Boolean).join(' · ') };
  });
}
export const eventMessage = (event: any) => isNewSeasonReleaseDate(event)
  ? `Release date: ${seasonReleaseDate(event) || 'Date unavailable'}`
  : isNewSeasonRelease(event)
    ? `${seasonLabel(event) || 'New season'} is now releasing`
    : isSeasonReleaseNotification(event) || event.kind === 'rating_needed' || (event.state === 'pending' && (eventCanChooseStatus(event) || event.kind === 'rating_conflict')) ? undefined : event.message;
export const seasonArtworkFallback = (event: any) => Boolean(isSeasonReleaseNotification(event) && event.payload?.season_artwork_fallback);
export const seasonArtworkRetry = (event: any) => Boolean(isSeasonReleaseNotification(event) && event.payload?.artwork_retry);
export const historyChanges = (event: any) => (event.payload?.changes || []).filter((change: any) => change.field !== 'status');
export const historyLabel = (field: string) => ({ start_date: 'Start date', finish_date: 'Finish date', progress: 'Progress', season: 'Season', episode: 'Episode', position: 'Playback position (ms)', duration: 'Duration (ms)' } as Record<string, string>)[field] || field;
export const historyValue = (value: any, field?: string) => value == null || value === '' ? 'Not set'
  : field === 'start_date' || field === 'finish_date' ? formatDate(String(value)) || 'Not set' : String(value);
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
        priority: value.priority, dismissible: value.dismissible, payload: value.payload, series_rating_request:value.series_rating_request,
        media: value.media && { id: value.media.id, title: value.media.title, type: value.media.type, poster: value.media.poster,
          runtime:value.media.runtime,year:value.media.year,season_count:value.media.season_count,episode_count:value.media.episode_count },
      }
    : {
        media_id: value.media_id, title: value.title, poster: value.poster, state: value.state,
        deliveries: value.deliveries,
      };
  return JSON.stringify(stableValue(selected));
}

export const shouldAutoSee = (event: any) => Boolean(event.dismissible && event.priority === 'low' && event.kind !== 'rating_needed' && !isSeasonReleaseNotification(event));
