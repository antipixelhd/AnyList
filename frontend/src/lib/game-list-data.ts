import type {EditorTitle} from './editor-store';
import {gameStatuses} from './game-editor-data.ts';

export function gameListQuery(params:URLSearchParams,defaultSort='title') {
  const query=new URLSearchParams();
  for(const key of ['q','genre']){const value=(params.get(key)||'').trim().slice(0,200);if(value)query.set(key,value);}
  const status=params.get('status');if(gameStatuses.some(([value])=>value===status))query.set('status',status!);
  const year=params.get('year');if(year&&/^\d{4}$/.test(year))query.set('year',year);
  const sort=params.get('sort')||defaultSort;
  query.set('sort',['title','score','progress','updated'].includes(sort)?sort:'title');
  if(params.get('favorites')==='true')query.set('favorites','true');
  return {query};
}
export function gameListEditorTitle(state:Record<string,any>):EditorTitle {
  return {id:state.subject.catalogue_id,subject:state.subject,type:'game',title:state.title,poster:state.image_url,
    year:state.year,genres:state.genres,game_class:state.game_class,base_work_id:state.base_work_id,
    game_state:state,entry:state.listed?state:null,favorite:state.favorite};
}
export function compareGameListEntries(left:Record<string,any>,right:Record<string,any>,sort:string){
  let order=0;
  if(sort==='score'||sort==='progress'){
    const field=sort==='score'?'score':'total_seconds';order=Number(right[field]??-1)-Number(left[field]??-1);
  }else if(sort==='updated')order=String(right.updated_at||'').localeCompare(String(left.updated_at||''));
  else{const a=left.title.toLowerCase(),b=right.title.toLowerCase();order=a<b?-1:a>b?1:0;}
  return order||(left.id??left.subject?.catalogue_id)-(right.id??right.subject?.catalogue_id);
}
