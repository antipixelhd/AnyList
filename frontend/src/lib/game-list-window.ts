/** Request batches are independent of the viewport's rendered row count. */
export const gameListPageSize=100;
export function gameListPages(start:number,end:number,total:number){
  const pages:number[]=[];
  if(end<=start)return pages;
  for(let offset=Math.floor(start/gameListPageSize)*gameListPageSize;offset<Math.min(total,end);offset+=gameListPageSize)pages.push(offset);
  return pages;
}

/** Position natural-height rows without imposing dimensions on the table. */
export class GameListGeometry {
  private heights=new Map<number,number>();
  private indices:number[]=[];
  private corrections:number[]=[];
  private dirty=false;
  readonly estimate:number;
  constructor(estimate:number){this.estimate=estimate;}
  measure(index:number,height:number){
    const previous=this.heights.get(index)??this.estimate;
    if(Math.abs(previous-height)<.1)return false;
    if(Math.abs(height-this.estimate)<.1)this.heights.delete(index);else this.heights.set(index,height);
    this.dirty=true;return true;
  }
  offset(index:number){
    if(this.dirty){
      this.indices=[...this.heights.keys()].sort((a,b)=>a-b);let sum=0;
      this.corrections=this.indices.map(key=>sum+=this.heights.get(key)!-this.estimate);this.dirty=false;
    }
    let low=0,high=this.indices.length;
    while(low<high){const mid=(low+high)>>>1;if(this.indices[mid]<index)low=mid+1;else high=mid;}
    return index*this.estimate+(low?this.corrections[low-1]:0);
  }
  window(top:number,viewport:number,count:number,ahead=3200){
    const at=(pixel:number)=>{
      let low=0,high=count;
      while(low<high){const mid=Math.ceil((low+high)/2);if(this.offset(mid)<=pixel)low=mid;else high=mid-1;}
      return low;
    };
    const start=at(Math.max(0,-top-1200));
    const end=Math.min(count,at(Math.max(0,viewport-top+ahead))+(viewport-top+ahead>0?1:0));
    return {start,end:Math.max(start,end)};
  }
}
