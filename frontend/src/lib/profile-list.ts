import {editorStore} from './editor-store';
import {showActionFailure} from './action-failure';
import {statuses} from './tracking-statuses';
import {paintScreenListRow,screenListEditorTitle} from './screen-list-row';
import {mountScreenListSeasons} from './screen-list-seasons';
import {initializeVirtualList} from './virtual-profile-list';
import {mountCategoryControls} from './list-category-controls';
import {type CategoryPresentation} from './list-categories.ts';

export function initializeProfileList(panel:HTMLElement,lifetime:AbortSignal){
  const root=panel.querySelector<HTMLElement>('[data-screen-scope]');
  const scope=root?.dataset.screenScope;
  if(!root||!scope)return;
  const presentation=JSON.parse(root.dataset.categoryPresentation || 'null') as CategoryPresentation | null;
  const incrementing=new Set<number>();
  if(presentation)mountCategoryControls(root,presentation,lifetime);
  initializeVirtualList(panel,lifetime,{
    statuses:presentation?.categories.map(category=>[category.id,category.label] as const) ?? statuses,
    accepts:title=>(title.type==='movie'||title.type==='series')&&(scope==='all'||scope===title.type),
    projected:title=>title.type==='series',
    editorTitle:screenListEditorTitle,
    updateState:(title,previous)=>({...previous,...title,...title.entry,id:title.id,listed:!!title.entry,
      title:title.title,favorite:!!title.favorite}),
    paint:(row,state,template)=>paintScreenListRow(row,state,template,editorStore.hasPendingSubject(state.subject||{kind:state.type,media_id:state.id})),
    editorControls:'[data-edit-entry],[data-quick-rate-id],[data-progress-increment]',
    mountRows(list,signal){
      const template=panel.querySelector<HTMLTemplateElement>('[data-list-season-template]');
      return template?mountScreenListSeasons(list,panel,template,signal):undefined;
    },
    onClick(target,title,state={}){
      const button=target.closest<HTMLButtonElement>('[data-progress-increment]');
      if(!button||button.disabled)return;
      const id=title.id!;
      if(incrementing.has(id))return;
      incrementing.add(id);
      const advance=async()=>{
        if(!state.list_season)return editorStore.write(title,{progress:Number(title.entry?.progress??0)+1});
        const summary=await editorStore.prepareSeries(id,lifetime);
        const episodes=summary.seasons.filter(season=>season.season_number>=state.list_season.number)
          .flatMap(season=>season.episodes);
        const next=episodes.find(episode=>!episode.watched);
        if(!next)return;
        return editorStore.write(title,{series_editor:{revision:summary.revision,order:summary.order,
          action:'episode',episode_id:next.id}},{progress:Number(title.entry?.progress??0)+1});
      };
      void advance().catch(error=>{
        if(!lifetime.aborted)showActionFailure('progress',title.title,error);
      }).finally(()=>incrementing.delete(id));
    },
  });
}
