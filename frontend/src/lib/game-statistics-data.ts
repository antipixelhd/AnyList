import type {ProfileStats} from './profile-stats-data';
import type {MetricGroup,StudioGroup,StaffGroup} from './stats-overview-data';
import {displayNumber} from './stats-overview-data.ts';

export const gameStatsSections=[['overview','Overview'],['genres','Genres'],['developers','Developers'],['publishers','Publishers'],['actors','Actors'],['staff','Staff']] as const;
export type GameStatsSection=typeof gameStatsSections[number][0];
export type GameStatsSort='count'|'mean_score'|'time';
export interface GameMetric {
  key:string;label:string;games:number;recorded_seconds:number|null;known_time_games:number;mean_score:number|null;rated_games:number;
  lower_seconds?:number;upper_seconds?:number|null;
}
export interface GameStatsGroup extends GameMetric {
  href:string|null;image:string|null;roles:string[];top_titles:{key:string;title:string;poster:string|null;href:string;score:number|null}[];
}
export interface GameOverview {
  domain:'game';totals:{listed_games:number;played_games:number;completed_games:number;recorded_seconds:number|null;known_time_games:number;
    mean_score:number|null;rated_games:number;standard_deviation:number|null;replays:number;replay_games:number;
    achievement_percentage:number|null;achievement_progress_games:number;achievement_count:number|null;achievement_count_games:number};
  planned:{games:number;estimated_seconds:number|null;known_estimate_games:number};statuses:{key:string;label:string;games:number}[];
  scores:GameMetric[];hours:GameMetric[];release_years:GameMetric[];completion_years:GameMetric[];
  comparisons:{key:string;label:string;games:number;mean_difference_seconds:number|null;mean_absolute_difference_seconds:number|null;mean_relative_percent:number|null}[];
  coverage:Record<string,number>;
}
export interface GameStatsResponse {
  profile:ProfileStats['profile'];owner:boolean;following:boolean;follows_you:boolean;combine_lists:boolean;domain:'game';
  section:GameStatsSection;status:'ready'|'pending'|'error';generation:number|null;computed_at:string|null;refreshing:boolean;
  data:GameOverview|GameStatsGroup[]|null;page:number;total:number|null;has_more:boolean;retry_after?:number;
}
export function gameStatsQuery(search:URLSearchParams) {
  const section=gameStatsSections.some(([key])=>key===search.get('section'))?search.get('section') as GameStatsSection:'overview';
  const sort:GameStatsSort=search.get('sort')==='mean_score'?'mean_score':search.get('sort')==='time'?'time':'count';
  const number=Number(search.get('page')||1),page=Number.isInteger(number)&&number>=1&&number<=5000?number:1;
  return {section,sort,page};
}
export function gameHeadlines(data:GameOverview|null) {
  const t=data?.totals,unknown=data?.coverage.unknown_time_games||0;
  return [
    {label:'Total games',value:t?displayNumber(t.listed_games):'—',detail:'Listed base games; expansions are excluded'},
    {label:'Completed games',value:t?displayNumber(t.completed_games):'—',detail:'Games currently marked Completed'},
    {label:'Hours recorded',value:t? t.recorded_seconds===null?(t.played_games?'—':'0'):`${unknown?'≥ ':''}${displayNumber(t.recorded_seconds/3600,1)}`:'—',detail:t?`${t.known_time_games} games with recorded time`:'Recorded time'},
    {label:'Mean score',value:displayNumber(t?.mean_score??null,2),detail:t?`${t.rated_games} rated played games`:'Personal ratings'},
    {label:'Achievement progress',value:t?.achievement_percentage==null?'—':`${displayNumber(t.achievement_percentage,1)}%`,detail:t?`Average across ${t.achievement_progress_games} games with known progress`:'Known progress only'},
    {label:'Achievements unlocked',value:displayNumber(t?.achievement_count??null),detail:t?`${t.achievement_count_games} games with known counts`:'Known counts only'},
  ];
}
export function gameChartRows(rows:GameMetric[]):MetricGroup[] {
  return rows.map(row=>({key:row.key,label:row.label,titles:row.games,minutes:(row.recorded_seconds??0)/60,
    mean_score:row.mean_score,rated_titles:row.rated_games,time_known:row.recorded_seconds!==null,
    ...(row.lower_seconds!==undefined?{detail:row.upper_seconds===null?`${row.lower_seconds/3600} hours or more`:
      `${row.lower_seconds/3600} to ${(row.upper_seconds??0)/3600} hours, upper bound excluded`}:{})}));
}
export function gameCard(row:GameStatsGroup):StudioGroup & StaffGroup {
  return {key:row.key,label:row.label,titles:row.games,minutes:(row.recorded_seconds??0)/60,mean_score:row.mean_score,
    rated_titles:row.rated_games,runtime_missing_plays:row.games-row.known_time_games,href:row.href||'',image:row.image,
    roles:row.roles,prominence:8,top_titles:row.top_titles.map(title=>({...title,roles:row.roles}))};
}
export function signedNumber(value:number|null,divisor=1) {
  return value===null?'—':`${value>0?'+':''}${displayNumber(value/divisor,1)}`;
}
