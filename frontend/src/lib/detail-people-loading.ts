import {detailBackground, type DetailLoadState} from './detail-background.ts';

type PeopleData = {people:{kind:string}[];credits_status?:string;character_status:DetailLoadState;primary_status?:DetailLoadState};

/** Repair credits independently; optional role artwork follows the usable preview. */
export function detailPeopleLoading<T extends PeopleData>(id:number, signal:AbortSignal,
  apply:(data:T)=>DetailLoadState|void, status:(state:'loading'|'ready'|'retry',error?:string)=>void,
  options:{selected:()=>boolean;needed:()=>boolean;primaryReady:()=>Promise<void>;warm:()=>void;
    source?:{people:string;characters?:string}}) {
  let primarySettled=false;
  const path=options.source?.people||`/api/proxy/tracking/title/${id}/people`;
  const separateCharacters=!options.source||!!options.source.characters;
  const credits=detailBackground<T>(path,signal,data=>{
    const state=apply(data);
    if(data.primary_status?.pending)return data.primary_status;
    if(!data.people.some(person=>person.kind==='actor')&&['failed','deferred'].includes(data.credits_status||''))
      return {state:'deferred',pending:true,retry_after:60,error:'unavailable'};
    return separateCharacters?{state:'ready'}:state;
  },status);
  const characters=separateCharacters?detailBackground<T>(options.source?.characters||`${path}?characters=true`,signal,data=>{
    const state=apply(data);
    if(primarySettled&&!signal.aborted)options.warm();
    return state;
  },(state,error)=>{
    if(options.selected())status(state,error);
  }):credits;
  const loadCharacters=async()=>{
    await credits.load();
    if(separateCharacters&&!signal.aborted&&options.selected()&&options.needed())await characters.load();
  };
  const start=async(missing:boolean)=>{
    if(!missing)await options.primaryReady();
    if(signal.aborted)return;
    await credits.load();
    if(signal.aborted)return;
    await options.primaryReady();
    if(signal.aborted)return;
    primarySettled=true;
    options.warm();
    if(separateCharacters&&options.needed())await characters.load();
  };
  return {start:(missing:boolean)=>void start(missing),
    characters:loadCharacters,retry:()=>options.selected()?characters.retry():credits.retry()};
}

/** Never let a stalled portrait indefinitely block lower-priority enrichment. */
export function waitForDetailPortraits(images:HTMLImageElement[], signal:AbortSignal, maxWaitMs=4000) {
  if(signal.aborted||images.every(image=>image.complete))return Promise.resolve();
  return new Promise<void>(resolve=>{
    const cleanup: (()=>void)[]=[];
    let remaining=images.filter(image=>!image.complete).length;
    const finish=()=>{clearTimeout(timer);cleanup.forEach(remove=>remove());signal.removeEventListener('abort',finish);resolve();};
    const timer=setTimeout(finish,maxWaitMs);
    signal.addEventListener('abort',finish,{once:true});
    for(const image of images.filter(image=>!image.complete)){
      const settled=()=>{
        image.removeEventListener('load',settled);image.removeEventListener('error',settled);
        if(--remaining===0)finish();
      };
      image.addEventListener('load',settled,{once:true});image.addEventListener('error',settled,{once:true});
      cleanup.push(()=>{image.removeEventListener('load',settled);image.removeEventListener('error',settled);});
    }
  });
}
