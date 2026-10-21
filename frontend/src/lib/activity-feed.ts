import {timestamp} from './date-time';

/** Shared cards retain their typed keys; games never collide with Media ids. */
export function mergeActivityFeeds(feeds:any[][],limit=60){
  const unique=new Map<string,any>();
  for(const row of feeds.flat()){
    const previous=unique.get(row.key);
    if(!previous||(timestamp(row.created_at)?.getTime()||0)>(timestamp(previous.created_at)?.getTime()||0))unique.set(row.key,row);
  }
  return [...unique.values()].sort((a,b)=>(timestamp(b.created_at)?.getTime()||0)-(timestamp(a.created_at)?.getTime()||0)||String(b.key).localeCompare(String(a.key))).slice(0,limit);
}

/** Only fields used by shared activity cards enter the private browser seed. */
export function activityPresentation(row:any){
  const fields=['status_changed','started_watching','rating_changed','rating_first','previous_score','finished_seasons','episodes_watched','position_start','position','progress','hours_changed','achievements_changed','added_to_list'];
  return {key:row.key,user_id:row.user_id,username:row.username,display_name:row.display_name,has_avatar:row.has_avatar,
    status:row.status,score:row.score,created_at:row.created_at,
    payload:Object.fromEntries(fields.filter(key=>key in (row.payload||{})).map(key=>[key,row.payload[key]])),
    media:{id:row.media.id,type:row.media.type,title:row.media.title,poster:row.media.poster}};
}
