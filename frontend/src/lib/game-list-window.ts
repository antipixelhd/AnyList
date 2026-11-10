/** Fixed row geometry keeps scroll extent independent of acquired pages. */
export const gameListPageSize=100;
export function gameListWindow(top:number,viewport:number,count:number,rowHeight:number,ahead=3200){
  const start=Math.max(0,Math.min(count,Math.floor((-top-1200)/rowHeight)));
  const end=Math.max(start,Math.min(count,Math.ceil((viewport-top+ahead)/rowHeight)));
  return {start,end};
}
export function gameListPages(start:number,end:number,total:number){
  const pages:number[]=[];
  if(end<=start)return pages;
  for(let offset=Math.floor(start/gameListPageSize)*gameListPageSize;offset<Math.min(total,end);offset+=gameListPageSize)pages.push(offset);
  return pages;
}
