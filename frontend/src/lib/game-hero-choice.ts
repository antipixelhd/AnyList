type Choice={url:string;pin:string|null};
const caches=new Map<string,Map<string,Choice>>();
const key=(viewer:string)=>`game-editor-heroes:${viewer}`;
const slot=(id:number,mobile:boolean)=>`${id}:${mobile?'mobile':'desktop'}`;
function choices(viewer:string){
  let values=caches.get(viewer);
  if(!values){
    try{values=new Map(JSON.parse(localStorage.getItem(key(viewer))||'[]'));}catch{values=new Map();}
    caches.set(viewer,values!);
  }
  return values!;
}
/** Only a successfully displayed detail hero writes history; editors never cycle it. */
export function rememberGameHero(viewer:string,id:number,mobile:boolean,url:string,pin:string|null){
  const values=choices(viewer),idKey=slot(id,mobile);
  values.delete(idKey);values.set(idKey,{url,pin});
  while(values.size>100)values.delete(values.keys().next().value!);
  try{localStorage.setItem(key(viewer),JSON.stringify([...values]));}catch{}
}
export function gameHeroChoice(state:Record<string,any>,viewer:string,mobile:boolean):string|undefined{
  const saved=choices(viewer).get(slot(state.subject?.catalogue_id??state.id,mobile));
  const pin=state.pinned_banner||null;
  if(saved&&saved.pin===pin&&typeof saved.url==='string')return saved.url;
  if(pin)return pin;
  return (mobile?state.backdrop_mobile||state.backdrop:state.backdrop)||undefined;
}
