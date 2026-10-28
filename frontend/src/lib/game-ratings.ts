import {detailBackground,type DetailLoadState} from './detail-background';
import {gameRatingRows,type GameRatings} from './game-ratings-data';
import {reveal} from './ui-motion';
import {paintGameSection,bindGameSection,gameSectionFailure} from './game-section-loading';

export function mountGameRatings(root:HTMLElement,id:number,signal:AbortSignal,initial?:DetailLoadState) {
  const section=root.querySelector<HTMLElement>('[data-game-ratings]');if(!section)return;
  const resource=detailBackground<{ratings:GameRatings|null;status:DetailLoadState}>(`/api/proxy/tracking/games/works/${id}/ratings`,signal,data=>{
    const rows=gameRatingRows(data.ratings),visible=section.hidden;
    for(const element of section.querySelectorAll<HTMLElement>('[data-game-rating-row]')){
      const row=rows.find(row=>row.field===element.dataset.gameRatingRow);element.hidden=!row;
      if(row){
        element.title=row.detail;element.querySelector('strong')!.textContent=row.value;
        const link=element.querySelector<HTMLAnchorElement>('a')!;
        if(row.url)link.href=row.url;else link.removeAttribute('href');
      }
    }
    paintGameSection(section,!!rows.length,data.status,root.dataset.gamePrimaryPending==='true');
    if(visible&&rows.length)reveal(section);
    return data.status;
  },(state,error)=>gameSectionFailure(section,state,error));
  bindGameSection(root,section,signal,resource,initial);
}
