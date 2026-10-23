export interface GameReviewSummary {
  positive:number;negative:number;total:number;percentage:number|null;description:string|null;language:'english';
  purchase_type:'steam';offtopic_filtered:boolean;window_start:number|null;window_end:number|null;fetched_at:string;
}
export interface GameRatings {
  steam_app_id:string|null;all_time?:GameReviewSummary;recent?:GameReviewSummary;
  critic?:{score:number|null;url:string|null;source:'steam_store'|'rawg';platform:'PC'|null;fetched_at?:string};
}
export interface GameRatingRow {field:string;name:string;value:string;detail:string;url?:string|null}
export const gameRatingFields=[['all_time','Steam · All time'],['recent','Steam · 30 days'],['critic','Metacritic']] as const;
export function gameRatingRows(data:GameRatings|null|undefined):GameRatingRow[] {
  if(!data)return [];
  return gameRatingFields.flatMap<GameRatingRow>(([field,name])=>{
    if(field==='critic'){
      const value=data.critic;
      if(!value||typeof value.score!=='number'||!Number.isInteger(value.score)||value.score<0||value.score>100)return [];
      let url:string|null=null;
      try {const parsed=new URL(value.url||'');if(parsed.protocol==='https:'&&['metacritic.com','www.metacritic.com'].includes(parsed.hostname)&&!parsed.username&&!parsed.password&&parsed.pathname.startsWith('/game/'))url=parsed.href;}catch{}
      const detail=value.source==='rawg'?'Metacritic · via RAWG':value.source==='steam_store'?'Metacritic · PC · via Steam':'Metacritic';
      return [{field,name,value:`${value.score}/100`,detail,url}];
    }
    const value=data[field];
    if(!data.steam_app_id||!/^\d+$/.test(data.steam_app_id)||!value||!Number.isInteger(value.total)||value.total<=0||!Number.isInteger(value.positive)||value.positive<0||value.positive>value.total)return [];
    const percentage=value.positive/value.total*100;
    const start=new Date(Number(value.window_start)*1000),end=new Date(Number(value.window_end)*1000);
    const window=field==='recent'&&value.window_start&&value.window_end&&Number.isFinite(start.getTime())&&Number.isFinite(end.getTime())&&start<end?` · ${start.toISOString()} to ${end.toISOString()}`:'';
    return [{field,name,value:`${new Intl.NumberFormat(undefined,{maximumFractionDigits:1}).format(percentage)}% positive`,
      detail:`${value.positive.toLocaleString()} of ${value.total.toLocaleString()} positive · English · Steam purchases${window}`,
      url:`https://store.steampowered.com/app/${data.steam_app_id}/#app_reviews_hash`}];
  });
}
