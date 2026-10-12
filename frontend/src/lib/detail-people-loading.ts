import {detailBackground, type DetailLoadState} from './detail-background.ts';

type PeopleData = {people:{kind:string}[];credits_status?:string;character_status:DetailLoadState};

/** Repair credits independently; role artwork is demand-only and follows credits. */
export function detailPeopleLoading<T extends PeopleData>(id:number, signal:AbortSignal,
  apply:(data:T)=>DetailLoadState|void, status:(state:'loading'|'ready'|'retry',error?:string)=>void,
  charactersSelected:()=>boolean) {
  const path=`/api/proxy/tracking/title/${id}/people`;
  const credits=detailBackground<T>(path,signal,data=>{
    apply(data);
    if(!data.people.some(person=>person.kind==='actor')&&['failed','deferred'].includes(data.credits_status||''))
      return {state:'deferred',pending:true,retry_after:60,error:'unavailable'};
    return {state:'ready'};
  },status);
  const characters=detailBackground<T>(`${path}?characters=true`,signal,apply,(state,error)=>{
    if(charactersSelected())status(state,error);
  });
  const loadCharacters=async()=>{
    await credits.load();
    if(!signal.aborted&&charactersSelected())await characters.load();
  };
  return {start:(missing:boolean)=>missing?void credits.load():credits.start(),
    characters:loadCharacters,retry:()=>charactersSelected()?characters.retry():credits.retry()};
}
