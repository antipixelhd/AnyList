type Season = {season_number:number; total:number; episodes:{watched:boolean}[];
  start_date?:string|null; completion_date?:string|null; start_manual?:boolean; date_manual?:boolean};
type Position = {season:number; count:number};
export type SeasonDates = {start_date?:string|null; completion_date?:string|null};

/** Defaults describe the season acted on, never the earlier cumulative coverage. */
export function presetSeasonDates(seasons:Season[], status:string, previous:string,
  selected:number|undefined, position:Position|undefined, today:string,
  drafts:Map<number,SeasonDates> = new Map()) {
  const result=new Map<number,SeasonDates>();
  const completing=status==='completed' && previous!=='completed';
  const target=completing?seasons.filter(s=>s.episodes.length).at(-1)
    :seasons.find(s=>s.season_number===(position?.season??selected));
  if(!target)return result;
  const draft=drafts.get(target.season_number);
  const count=position?.season===target.season_number?position.count:target.episodes.filter(ep=>ep.watched).length;
  const latestSeen=seasons.filter(s=>s.episodes.some(ep=>ep.watched)).at(-1)?.season_number??0;
  const advanced=Boolean(position && (count>target.episodes.filter(ep=>ep.watched).length ||
    status==='watching' && target.season_number>latestSeen));
  const starting=status==='watching' && previous!=='watching';
  const dates:SeasonDates={};
  if((starting || advanced && !completing) && !target.start_date && !target.start_manual && !(draft && 'start_date' in draft))
    dates.start_date=draft?.completion_date||today;
  const full=target.total>0 && target.total===target.episodes.length && (completing || count===target.total);
  if(full && (completing||advanced) && !target.completion_date && !target.date_manual && !(draft && 'completion_date' in draft))
    dates.completion_date=today;
  if(Object.keys(dates).length)result.set(target.season_number,dates);
  return result;
}
