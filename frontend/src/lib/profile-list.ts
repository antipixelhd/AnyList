import {editorStore} from './editor-store';
import {showActionFailure} from './action-failure';
import {statuses} from './tracking-statuses';
import {paintScreenListRow,screenListEditorTitle} from './screen-list-row';
import {initializeVirtualList} from './virtual-profile-list';
import {mountCategoryControls} from './list-category-controls';
import {type CategoryPresentation} from './list-categories.ts';

export function initializeProfileList(panel:HTMLElement,lifetime:AbortSignal){
  const root=panel.querySelector<HTMLElement>('[data-screen-scope]');
  const scope=root?.dataset.screenScope;
  if(!root||!scope)return;
  const presentation=JSON.parse(root.dataset.categoryPresentation || 'null') as CategoryPresentation | null;
  if(presentation)mountCategoryControls(root,presentation,lifetime);
  initializeVirtualList(panel,lifetime,{
    statuses:presentation?.categories.map(category=>[category.id,category.label] as const) ?? statuses,
    accepts:title=>(title.type==='movie'||title.type==='series')&&(scope==='all'||scope===title.type),
    editorTitle:screenListEditorTitle,
    updateState:(title,previous)=>({...previous,...title,...title.entry,id:title.id,listed:!!title.entry,
      title:title.title,favorite:!!title.favorite}),
    paint:(row,state,template)=>paintScreenListRow(row,state,template,editorStore.hasPendingSubject(state.subject||{kind:state.type,media_id:state.id})),
    editorControls:'[data-edit-entry],[data-quick-rate-id],[data-progress-increment]',
    onClick(target,title){
      const button=target.closest<HTMLButtonElement>('[data-progress-increment]');
      if(!button||button.disabled)return;
      void editorStore.write(title,{progress:Number(title.entry?.progress??0)+1}).catch(error=>{
        if(!lifetime.aborted)showActionFailure('progress',title.title,error);
      });
    },
  });
}
