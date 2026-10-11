export type DetailEpisode = {id:number;episode_number:number;title:string;air_date?:string|null;runtime?:number|null;image?:string|null;watched:boolean;released:boolean};
export type DetailSeason = {season_number:number;name?:string;air_date?:string|null;episode_count:number;released_count:number|null;watched_count:number;episodes?:DetailEpisode[]};

/** Follow the latest season with watch history rather than reopening older gaps. */
export function currentDetailSeason(seasons:DetailSeason[],watching=false) {
  const regular=[...seasons].filter(season=>season.season_number>0).sort((a,b)=>a.season_number-b.season_number);
  const watchedSeason=regular.filter(season=>season.episodes?.some(episode=>episode.watched)).at(-1)?.season_number;
  const latest=watchedSeason??1;
  return regular.find(season=>season.season_number>=latest&&season.episodes?.some(episode=>episode.released&&!episode.watched))?.season_number ?? (watching?watchedSeason??null:null);
}

export function detailSeasonState(season:DetailSeason,current:number|null,watching:boolean) {
  const watched=!!season.released_count&&season.watched_count>=season.released_count;
  const progress=watching&&season.season_number===current&&!watched&&!!season.released_count;
  return {watched,progress,label:watched?'Watched':progress?`${season.watched_count} / ${season.released_count} watched`:'Not watched'};
}
