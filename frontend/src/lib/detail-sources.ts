export function detailSources(rows:{name:string;url:string}[] = []) {
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
  return [...sources.values()];
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
  const sources=detailSources([...sections.values()].flat());
  nav.replaceChildren(...sources.map(source=>{
    const link=document.createElement('a');link.href=source.url;link.target='_blank';link.rel='noreferrer';link.textContent=source.name;
    const icon=root.querySelector<HTMLTemplateElement>('[data-detail-source-icon]');
    if(icon)link.append(icon.content.cloneNode(true));
    return link;
  }));nav.hidden=!sources.length;
}
