import {calendarDate, localToday} from '../../lib/date-time.ts';

export type Season = {
  number: number; total: number; watched: number; completed: boolean;
  date: string; source: 'connection' | 'automatic' | 'manual' | 'inferred' | null;
  inferred: string; score?: string;
};
export type PreviewMedia = 'series' | 'movie' | 'game';
export type PreviewEntry = {
  seasons: Season[]; score: string; notes: string; start: string; finish: string; favorite: boolean;
  status: string; rewatches: number; hours: number; replays: number; firstHours: string; achievements: string;
};

export function initialEntry(): PreviewEntry {
  return {score:'8.5', notes:'', start:'2026-09-25', finish:'', favorite:false,
    status:'watching', rewatches:0, hours:12.5, replays:0, firstHours:'', achievements:'', seasons:[
    {number:1,total:7,watched:7,completed:true,date:'2026-10-02',source:'connection',inferred:'',score:'8'},
    {number:2,total:13,watched:13,completed:true,date:'',source:null,inferred:'2026-10-10',score:'8.5'},
    {number:3,total:13,watched:12,completed:false,date:'',source:null,inferred:''},
    {number:4,total:13,watched:0,completed:false,date:'',source:null,inferred:''},
    {number:5,total:16,watched:0,completed:false,date:'',source:null,inferred:''},
  ]};
}

export const nextSeason = (entry: PreviewEntry) => entry.seasons.find(season => !season.completed);
export function initialWork(kind: PreviewMedia): PreviewEntry {
  const entry = initialEntry();
  if (kind !== 'series') entry.seasons = [];
  if (kind === 'movie') {entry.status = 'completed'; entry.start = '2026-10-18'; entry.finish = '2026-10-18';}
  if (kind === 'game') {entry.status = 'playing'; entry.start = '2026-10-20';}
  return entry;
}
export const totalProgress = (entry: PreviewEntry) => entry.seasons.reduce((sum, season) => sum + season.watched, 0);

/** Fixture-only transitions. No production editor, account, importer or API writes. */
export function completeSeason(season: Season, today = localToday()) {
  if (season.completed) return;
  season.completed = true;
  season.watched = season.total;
  if (!season.date) {season.date = today; season.source = 'automatic';}
}

export function advanceEpisode(entry: PreviewEntry, today = localToday()) {
  const season = nextSeason(entry);
  if (!season) return;
  season.watched = Math.min(season.total, season.watched + 1);
  if (season.watched === season.total) completeSeason(season, today);
}

export function setPreviewProgress(entry: PreviewEntry, progress: number, today = localToday()) {
  let remaining = Math.max(0, Math.min(entry.seasons.reduce((sum, season) => sum + season.total, 0), Math.floor(progress)));
  for (const season of entry.seasons) {
    const watched = Math.min(remaining, season.total);
    remaining -= watched;
    if (watched === season.total) completeSeason(season, today);
    else if (season.completed) {season.completed = false; season.date = ''; season.source = null;}
    season.watched = watched;
  }
}

export function setSeasonDate(season: Season, date: string, source: Season['source'] = 'manual') {
  if (date && !calendarDate(date)) throw new Error('Invalid completion date');
  season.date = date;
  season.source = date ? source : null;
  // An unknown completion date is still a completed season.
  if (date) {season.completed = true; season.watched = season.total;}
}
