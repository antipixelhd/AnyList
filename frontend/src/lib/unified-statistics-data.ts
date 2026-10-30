import type {Overview,OverviewResponse,MediaScope} from './stats-overview-data';
import {gameCard,gameChartRows,type GameStatsResponse,type GameStatsGroup,type GameOverview} from './game-statistics-data.ts';

export type StatisticsDomain='screen'|'game';
export type UnifiedStatistics=OverviewResponse & {game?:GameStatsResponse;selectionSort?:string};
export const statisticsSections={screen:['overview','genres','actors','studios','staff'],game:['overview','genres','developers','publishers','actors','staff']};
export function statisticsSection(domain:StatisticsDomain,section:string){return statisticsSections[domain].includes(section)?section:'overview';}
export function statisticsPanel(section:string){return section==='developers'||section==='publishers'?'studios':section;}
export function emptyStatistics(media:MediaScope='all'):Overview{
  return {media_type:media,totals:{listed_titles:0,watched_titles:0,episode_plays:0,distinct_episodes:0,watch_minutes:0,watch_days:0,planned_minutes:0,planned_days:0,mean_score:null,standard_deviation:null,rated_titles:0},scores:[],episode_counts:[],statuses:[],formats:[],countries:[],release_years:[],watch_years:[],coverage:{}};
}
/** Presentation projection only: keep the complete Games contract alongside it. */
export function gameStatisticsView(game:GameStatsResponse,selectionSort='count'):UnifiedStatistics{
  const view=emptyStatistics();
  if(game.status==='ready'){
    if(game.section==='overview'){
      const data=game.data as GameOverview;
      view.totals={...view.totals,listed_titles:data.totals.listed_games,watched_titles:data.totals.completed_games,watch_minutes:(data.totals.recorded_seconds||0)/60,mean_score:data.totals.mean_score,standard_deviation:data.totals.standard_deviation,rated_titles:data.totals.rated_games};
      view.scores=gameChartRows(data.scores);view.episode_counts=gameChartRows(data.hours);view.release_years=gameChartRows(data.release_years);view.watch_years=gameChartRows(data.completion_years);
      view.statuses=data.statuses.map(row=>({key:row.key,label:row.label,titles:row.games}));view.coverage=data.coverage;
    }else{
      const rows=(game.data as GameStatsGroup[]).slice(0,30).map(gameCard);
      if(game.section==='genres')view.genres=rows.map(row=>({...row,browse_filters:{movie:'',series:''}}));
      else if(game.section==='actors')view.actors=rows.map(row=>({...row,top_titles:row.top_titles.map(title=>({...title,character:null,character_image:null}))}));
      else if(game.section==='staff')view.staff=rows;
      else view.studios=rows;
    }
  }
  return {...game,selectionSort,status:game.status,overview:game.status==='ready'?view:null,game,next_update_at:new Date(Date.now()+(game.retry_after||3)*1000).toISOString()};
}
