export type DetailEpisode = {id:number;episode_number:number;title:string;air_date?:string|null;runtime?:number|null;image?:string|null;watched:boolean;released:boolean};
export type DetailSeason = {season_number:number;name?:string;air_date?:string|null;episode_count:number;released_count:number|null;watched_count:number;episodes?:DetailEpisode[]};

export function visibleDetailSeasons(seasons:DetailSeason[], removeSpecials=true) {
  return seasons.filter(season=>!removeSpecials||season.season_number!==0).sort((a,b)=>a.season_number-b.season_number);
}

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

/** Project contiguous progress only when the released episode catalogue is complete. */
export function optimisticSeasonProgress(seasons:DetailSeason[], progress:number):DetailSeason[] | null {
  if (!Number.isInteger(progress) || progress < 0) return null;
  const regular = [...seasons].filter(season => season.season_number > 0).sort((a,b) => a.season_number-b.season_number);
  if (!regular.length || regular.some(season => season.released_count == null ||
    (season.episodes || []).filter(episode => episode.released).length !== season.released_count)) return null;
  const total = regular.reduce((count, season) => count + season.released_count!, 0);
  if (progress > total) return null;
  let position = 0;
  const byNumber = new Map(regular.map(season => {
    const episodes = [...(season.episodes || [])].sort((a,b) => a.episode_number-b.episode_number).map(episode => ({
      ...episode, watched: episode.released ? ++position <= progress : episode.watched,
    }));
    return [season.season_number, {...season, episodes, watched_count:episodes.filter(episode=>episode.released && episode.watched).length}];
  }));
  return seasons.map(season => byNumber.get(season.season_number) || season);
}
