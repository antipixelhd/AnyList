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
