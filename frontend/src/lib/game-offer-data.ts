import {moneyLabel} from './media-detail-data.ts';
import type {DetailLoadState} from './detail-background';

export interface GameOffer {
  steam_app_id:string;country:string;current:{currency?:string;amount?:number;amountInt?:number}|null;
  historical_low:GameOffer['current'];historical_low_at:string|null;url:string|null;fetched_at:string;
}
export interface GameOffers {
  country:string;country_source:'account'|'device'|'fallback';steam_app_id:string|null;offers:GameOffer[];status:DetailLoadState;
}
export function offerRegionMatches(source:string|undefined,country:string|undefined,detected:string) {
  return source==='account'||source==='device'&&country===detected;
}
export function selectedGameOffer(data:GameOffers|null|undefined,ssr=false) {
  if(!data?.steam_app_id||!/^\d+$/.test(data.steam_app_id)||ssr&&data.country_source==='fallback')return null;
  return data.offers.find(row=>row.country===data.country&&row.steam_app_id===data.steam_app_id&&moneyLabel(row.current)!==null)||null;
}
export function offerLink(value:string|null) {
  if(!value)return null;
  try {const url=new URL(value);return url.protocol==='https:'&&url.hostname&&!url.username&&!url.password?url.href:null;}
  catch{return null;}
}
