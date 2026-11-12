// Compact wire projection: no notes, artwork, achievements or editor revisions.
export type ListIndexRow=[number,string,string,number|null,number|null,string|null,string|null,string[],boolean,number|null,number];
import type {CategoryPresentation} from './list-categories.ts';
export type ListIndexResponse={category_presentation?:CategoryPresentation;complete:boolean;results:ListIndexRow[]};
export type ListState=Record<string,any>;
export const entryId=(state:ListState):number=>state.subject?.catalogue_id??state.id;
type Member={id:number;title:string;status:string;score:number|null;progress:number|null;updated:string;year:string|null;genres:string[];favorite:boolean;base:number|null;rank:number;listed:boolean};
// Server timestamps are UTC. Preserve microprogress, which Date.parse discards.
const timestamp=(value:string|null)=>value?value.replace(/(\.\d+)?(?:Z|\+00:00)?$/,(_match,fraction='')=>(fraction||'.').padEnd(7,'0')):'';

/** Only a complete, viewer-scoped snapshot can answer whole-library queries. */
export class ProfileListIndex {
  private members=new Map<number,Member>();
  private complete=false;
  private order:Map<string,number>;
  constructor(statuses:ReadonlyArray<readonly [string,string]>){
    this.order=new Map(statuses.map(([status],index)=>[status,index]));
  }
  setOrder(statuses:ReadonlyArray<readonly [string,string]>){
    this.order=new Map(statuses.map(([status],index)=>[status,index]));
  }
  get ready(){return this.complete;}
  publish(snapshot:ListIndexResponse){
    this.invalidate();
    if(!snapshot.complete)return false;
    for(const [id,title,status,score,progress,updated,year,genres,favorite,base,rank] of snapshot.results)
      this.members.set(id,{id,title,status,score,progress,updated:timestamp(updated),year,genres,favorite,base,rank,listed:true});
    this.complete=true;return true;
  }
  invalidate(){this.complete=false;this.members.clear();}
  update(state:ListState){
    if(!this.complete)return;
    const member=this.members.get(entryId(state));
    // A new title needs a fresh server collation rank. Never guess its position.
    if(!member){if(state.listed)this.invalidate();return;}
    Object.assign(member,{status:state.status,score:state.score,progress:state.subject?.kind==='game'||state.subject?.catalogue_id?state.total_seconds:state.progress,
      updated:timestamp(state.updated_at),favorite:!!state.favorite,listed:!!state.listed});
  }
  select(query:URLSearchParams){
    // PostgreSQL case conversion is authoritative for non-ASCII searches.
    if(!this.complete||/[^\x00-\x7f]/.test(query.get('q')||''))return null;
    const eligible=[...this.members.values()].filter(row=>row.listed&&(!row.base||this.members.get(row.base)?.listed));
    const status_counts:Record<string,number>={},group_counts:Record<string,number>={};
    for(const row of eligible)status_counts[row.status]=(status_counts[row.status]||0)+1;
    const q=(query.get('q')||'').toLowerCase(),sort=query.get('sort')||'title';
    const matches=eligible.filter(row=>(!query.get('status')||row.status===query.get('status'))&&row.title.includes(q)&&
      (!query.get('genre')||row.genres.includes(query.get('genre')!))&&(!query.get('year')||row.year===query.get('year'))&&
      (!query.has('favorites')||row.favorite));
    matches.sort((a,b)=>{
      const group=(this.order.get(a.status)??99)-(this.order.get(b.status)??99);if(group)return group;
      if(sort==='title')return a.rank-b.rank;
      if(sort==='updated')return (a.updated<b.updated?1:a.updated>b.updated?-1:0)||a.id-b.id;
      const value=(row:Member)=>sort==='score'?row.score:row.progress;
      const av=value(a),bv=value(b);
      return (av==null?bv==null?0:1:bv==null?-1:bv-av)||a.id-b.id;
    });
    for(const row of matches)group_counts[row.status]=(group_counts[row.status]||0)+1;
    return {ids:matches.map(row=>row.id),total:matches.length,status_counts,group_counts,
      genres:[...new Set(eligible.flatMap(row=>row.genres))].sort(),
      years:[...new Set(eligible.map(row=>row.year).filter((year):year is string=>!!year))].sort().reverse()};
  }
}
