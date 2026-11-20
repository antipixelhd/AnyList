import {returnViewLifetime} from './return-view-cache.ts';

const storageKey = 'anylist:profile-tabs:v2';
const legacyStorageKey = 'anylist:profile-tabs:v1';
const budget = 2 * 1024 * 1024;
const limit = 8;
type Seed = {title:string; content:string; styles:string; checkedAt:number};
type Entry = Seed & {key:string; bytes:number};

// Storage is optional. A single tab owns these snapshots, and only the
// authenticated profile shell can restore them after a fresh server response.
export function createProfileSessionCache(storage:Storage, identity:string, now=Date.now) {
  let entries:Entry[] = [];
  try {
    // Version one omitted production inline CSS and cannot restore a usable view.
    storage.removeItem(legacyStorageKey);
    const stored=JSON.parse(storage.getItem(storageKey)||'null');
    if(stored?.identity===identity&&Array.isArray(stored.entries))entries=stored.entries;
  }catch { /* Storage may be unavailable. */ }
  const prune=()=>{entries=entries.filter(entry=>now()-entry.checkedAt<returnViewLifetime);};
  const save=()=>{
    try {
      if(entries.length)storage.setItem(storageKey,JSON.stringify({identity,entries}));
      else storage.removeItem(storageKey);
    }catch { /* A storage quota or browser setting can disable persistence. */ }
  };
  prune();
  return {
    get(key:string){prune();return entries.find(entry=>entry.key===key);},
    delete(key:string){entries=entries.filter(entry=>entry.key!==key);},
    set(key:string,seed:Seed){
      prune();
      const bytes=new Blob([JSON.stringify(seed)]).size;
      if(bytes>budget||now()-seed.checkedAt>=returnViewLifetime)return;
      entries=entries.filter(entry=>entry.key!==key);
      entries.push({...seed,key,bytes});
      let total=entries.reduce((sum,entry)=>sum+entry.bytes,0);
      while(entries.length>limit||total>budget)total-=entries.shift()!.bytes;
    },
    flush:save,
    clear(){entries=[];save();},
  };
}

let current:ReturnType<typeof createProfileSessionCache>|undefined;
let currentIdentity:string|undefined;
export function profileSessionCache(identity:string) {
  if(currentIdentity!==identity){
    currentIdentity=identity;
    try{current=createProfileSessionCache(sessionStorage,identity);}catch{current=undefined;}
  }
  return current;
}

export function clearProfileSessionCache(){
  current?.clear();
  try{sessionStorage.removeItem(storageKey);sessionStorage.removeItem(legacyStorageKey);}catch { /* Optional storage. */ }
}
