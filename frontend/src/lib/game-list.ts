import {editorStore,type EditorTitle} from './editor-store';
import {gameListEditorTitle} from './game-list-data';
import {paintGameListRow} from './game-list-row';
import {defaultGameCategories,type CategoryPresentation} from './list-categories.ts';
import {mountCategoryControls} from './list-category-controls.ts';
import {initializeVirtualList} from './virtual-profile-list';

export function initializeGameList(panel:HTMLElement,lifetime:AbortSignal){
  const root=panel.querySelector<HTMLElement>('[data-virtual-list]');
  const presentation:CategoryPresentation=root?JSON.parse(root.dataset.categoryPresentation||'null')||defaultGameCategories():defaultGameCategories();
  if(root)mountCategoryControls(root,presentation,lifetime);
  initializeVirtualList(panel,lifetime,{
    statuses:presentation.categories.map(category=>[category.id,category.label] as const),
    accepts:title=>title.type==='game',
    editorTitle:gameListEditorTitle,
    updateState:(title,previous)=>({...previous,...title.game_state,...title.entry,subject:title.subject,
      title:title.title,image_url:title.poster,year:title.year,genres:title.genres,
      game_class:title.game_class,base_work_id:title.base_work_id,listed:!!title.entry,favorite:!!title.favorite}),
    paint:(row,state,template)=>paintGameListRow(row,state,template,editorStore.hasPendingSubject(state.subject)),
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
