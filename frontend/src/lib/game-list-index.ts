import {gameStatuses} from './game-editor-data.ts';

// Compact wire projection: no notes, artwork, achievements or editor revisions.
export type GameListIndexRow=[number,string,string,number|null,number|null,string|null,string|null,string[],boolean,number|null,number];
export type GameListIndexResponse={complete:boolean;results:GameListIndexRow[]};
export type GameListState=Record<string,any>;
type Member={id:number;title:string;status:string;score:number|null;seconds:number|null;updated:string;year:string|null;genres:string[];favorite:boolean;base:number|null;rank:number;listed:boolean};
// Server timestamps are UTC. Preserve microseconds, which Date.parse discards.
const timestamp=(value:string|null)=>value?value.replace(/(\.\d+)?(?:Z|\+00:00)?$/,(_match,fraction='')=>(fraction||'.').padEnd(7,'0')):'';

/** Only a complete, viewer-scoped snapshot can answer whole-library queries. */
export class GameListIndex {
  private members=new Map<number,Member>();
  private complete=false;
  get ready(){return this.complete;}
  publish(snapshot:GameListIndexResponse){
    this.invalidate();
    if(!snapshot.complete)return false;
    for(const [id,title,status,score,seconds,updated,year,genres,favorite,base,rank] of snapshot.results)
      this.members.set(id,{id,title,status,score,seconds,updated:timestamp(updated),year,genres,favorite,base,rank,listed:true});
    this.complete=true;return true;
  }
  invalidate(){this.complete=false;this.members.clear();}
  update(state:GameListState){
    if(!this.complete)return;
    const member=this.members.get(state.subject.catalogue_id);
    // A new title needs a fresh server collation rank. Never guess its position.
    if(!member){if(state.listed)this.invalidate();return;}
    Object.assign(member,{status:state.status,score:state.score,seconds:state.total_seconds,
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
    const order=new Map(gameStatuses.map(([status],index)=>[status,index]));
    matches.sort((a,b)=>{
      const group=order.get(a.status)!-order.get(b.status)!;if(group)return group;
      if(sort==='title')return a.rank-b.rank;
      if(sort==='updated')return (a.updated<b.updated?1:a.updated>b.updated?-1:0)||a.id-b.id;
      const value=(row:Member)=>sort==='score'?row.score:row.seconds;
      const av=value(a),bv=value(b);
      return (av==null?bv==null?0:1:bv==null?-1:bv-av)||a.id-b.id;
    });
    for(const row of matches)group_counts[row.status]=(group_counts[row.status]||0)+1;
    return {ids:matches.map(row=>row.id),total:matches.length,status_counts,group_counts,
      genres:[...new Set(eligible.flatMap(row=>row.genres))].sort(),
      years:[...new Set(eligible.map(row=>row.year).filter((year):year is string=>!!year))].sort().reverse()};
  }
}
