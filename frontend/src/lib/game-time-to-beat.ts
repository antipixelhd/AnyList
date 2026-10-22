import {detailBackground} from './detail-background.ts';

export interface CompletionEstimates {
  main_story_seconds:number|null;main_sides_seconds:number|null;completionist_seconds:number|null;
  source_game_id:string;source:'igdb';samples:number|null;fetched_at?:string;
}
export const completionCategories=[['main_story_seconds','Main Story'],['main_sides_seconds','Main + Sides'],['completionist_seconds','Completionist']] as const;

export function estimatedHours(seconds:unknown):string|null {
  if(typeof seconds!=='number'||!Number.isFinite(seconds)||seconds<=0)return null;
  const hours=seconds/3600;
  const minutes=Math.round(seconds/60);
  return minutes<60?`${Math.max(1,minutes)}m`:`${new Intl.NumberFormat(undefined,{maximumFractionDigits:1}).format(hours)}h`;
}

export function mountGameTimeToBeat(root:HTMLElement,id:number,signal:AbortSignal) {
  const section=root.querySelector<HTMLElement>('[data-game-time-to-beat]');
  if(!section)return;
  const resource=detailBackground<{estimates:CompletionEstimates|null;status:import('./detail-background').DetailLoadState}>(
    `/api/proxy/tracking/games/works/${id}/time-to-beat`,signal,data=>{
      const rows=completionCategories.map(([field])=>{
        const item=section.querySelector<HTMLElement>(`[data-time-estimate=${field}]`)!;
        const value=estimatedHours(data.estimates?.[field]);item.hidden=!value;
        item.querySelector('dd')!.textContent=value||'';return !!value;
      });
      section.hidden=!rows.some(Boolean);
      const source=section.querySelector<HTMLElement>('[data-time-estimate-source]')!;
      source.title=data.estimates?.samples!=null?`${data.estimates.samples} IGDB submissions`:'';
      return data.status;
    },()=>{});
  resource.start();
}
