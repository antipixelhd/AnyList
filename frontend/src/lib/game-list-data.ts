import type {EditorTitle} from './editor-store';
import {gameStatuses} from './game-editor-data.ts';

import {profileListQuery} from './profile-list-query.ts';

export const gameListQuery=(params:URLSearchParams,defaultSort='title')=>({query:profileListQuery(params,gameStatuses,defaultSort)});
export function gameListEditorTitle(state:Record<string,any>):EditorTitle {
  return {id:state.subject.catalogue_id,subject:state.subject,type:'game',title:state.title,poster:state.image_url,
    year:state.year,genres:state.genres,game_class:state.game_class,base_work_id:state.base_work_id,
    game_state:state,entry:state.listed?state:null,favorite:state.favorite};
}
