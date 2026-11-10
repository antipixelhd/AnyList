import {editorStore,type EditorTitle} from './editor-store';
import {gameListEditorTitle} from './game-list-data';
import {paintGameListRow} from './game-list-row';
import {gameStatuses} from './game-editor-data';
import {initializeVirtualList} from './virtual-profile-list';

export function initializeGameList(panel:HTMLElement,lifetime:AbortSignal){
  initializeVirtualList(panel,lifetime,{
    statuses:gameStatuses,
    accepts:title=>title.type==='game',
    editorTitle:gameListEditorTitle,
    updateState:(title,previous)=>({...previous,...title.game_state,...title.entry,subject:title.subject,
      title:title.title,image_url:title.poster,year:title.year,genres:title.genres,
      game_class:title.game_class,base_work_id:title.base_work_id,listed:!!title.entry,favorite:!!title.favorite}),
    paint:paintGameListRow,
    editorControls:'[data-edit-game],[data-game-list-rate]',
    removeChildren:true,
    onClick(target:Element,title:EditorTitle){
      const button=target.closest<HTMLButtonElement>('[data-game-list-rate]');if(!button)return;
      if(title.game_class==='expansion'){
        document.dispatchEvent(new CustomEvent('anylist:open-editor',{detail:{subject:title.subject,opener:button,title}}));
        return;
      }
      (window as any).anyListQuickRate?.({title:title.title,poster:title.poster,score:title.entry?.score,ratingMode:'manual',
        onScore:async(score:number|null)=>{await editorStore.write(title,{score});}});
    },
  });
}
