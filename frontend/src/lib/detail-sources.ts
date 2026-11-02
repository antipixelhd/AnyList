export function detailSources(rows:{name:string;url:string}[] = [], game=false) {
  const sources=new Map<string,{name:string;url:string}>();
  for(const row of rows){
    try{
      const url=new URL(row.url);
      if(!['http:','https:'].includes(url.protocol))continue;
      const app=url.hostname==='store.steampowered.com'&&url.pathname.match(/^\/app\/(\d+)(?:\/|$)/);
      const source=app?{name:'Steam',url:`https://store.steampowered.com/app/${app[1]}/`}
        :url.hostname==='metacritic.com'||url.hostname==='www.metacritic.com'?{...row,name:'Metacritic'}:row;
      const key=app?source.url:`${url.origin}${url.pathname.replace(/\/$/,'')}${url.search}`;
      if(!sources.has(key))sources.set(key,source);
    }catch{}
  }
  const values=[...sources.values()];
  if(!game)return values;
  const official=values.filter(row=>row.name==='Official website').slice(0,1);
  const steam=values.filter(row=>new URL(row.url).hostname==='store.steampowered.com').slice(0,1);
  const igdb=values.filter(row=>['igdb.com','www.igdb.com'].includes(new URL(row.url).hostname)).slice(0,1).map(row=>({...row,name:'IGDB'}));
  const art=steam.map(row=>({name:'SteamGridDB · Art',url:`https://www.steamgriddb.com/steam/${new URL(row.url).pathname.split('/')[2]}`}));
  return [...official,...steam,...igdb,...art];
}

const publications=new WeakMap<HTMLElement,Map<string,{name:string;url:string}[]>>();
export function publishDetailSources(root:HTMLElement,rows:{name:string;url:string}[],owner='primary') {
  const nav=root.querySelector<HTMLElement>('[data-detail-sources]');if(!nav)return;
  let sections=publications.get(root);
  if(!sections){
    sections=new Map([['primary',[...nav.querySelectorAll<HTMLAnchorElement>('a')].map(link=>({name:link.textContent||'',url:link.href}))]]);
    publications.set(root,sections);
  }
  sections.set(owner,rows);
  const sources=detailSources([...sections.values()].flat(),root.dataset?.mediaKind==='game');
  nav.replaceChildren(...sources.map(source=>{
    const link=document.createElement('a');link.href=source.url;link.target='_blank';link.rel='noreferrer';link.textContent=source.name;
    const icon=root.querySelector<HTMLTemplateElement>('[data-detail-source-icon]');
    if(icon)link.append(icon.content.cloneNode(true));
    return link;
  }));nav.hidden=!sources.length;
}
