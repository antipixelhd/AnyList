import {detailBrowseHref,detailInformationRows,detailSynopsis,prepareDetail,type DetailInformationRow} from './media-detail-data';
import {mountContributorBiography,unmountContributorBiography} from './contributor-biography';
import {type DetailLoadState} from './detail-background';

/** Public-only reconciliation. Editors, season progress and selected art keep their owners. */
export function publishScreenPrimary(root:HTMLElement,data:Record<string,any>,id:number,status:DetailLoadState,signal:AbortSignal) {
  if(signal.aborted||data.id!==id||data.type!==root.dataset.mediaKind||!['movie','series'].includes(data.type))return false;
  const title=prepareDetail(data),pending=status.pending===true;
  root.dataset.screenPrimaryPending=String(pending);
  for(const [selector,copy] of [['[data-screen-original-title]',title.original_title?.trim().toLocaleLowerCase()!==title.title.trim().toLocaleLowerCase()?title.original_title:''],['[data-screen-tagline]',title.tagline]]){
    const slot=root.querySelector<HTMLElement>(selector!);
    if(slot&&copy&&!slot.textContent?.trim()){slot.textContent=copy;slot.hidden=false;}
  }
  const rowNode=(row:DetailInformationRow)=>{
    const node=document.createElement('div'),term=document.createElement('dt'),value=document.createElement('dd');
    node.dataset.detailFact=row.label;node.dataset.factSignature=JSON.stringify(row);term.textContent=row.label;
    if(row.pending){const skeleton=document.createElement('span');skeleton.className='detail-value-skeleton';skeleton.setAttribute('aria-hidden','true');node.dataset.factPending='true';value.append(skeleton);}
    else if(row.items)row.items.forEach(person=>{
      const entry=document.createElement('span');entry.className='media-fact-entry';
      let link:URL|undefined;
      try{if(person.url){const candidate=new URL(person.url,location.origin);if(candidate.origin===location.origin||candidate.protocol==='https:')link=candidate;}}catch{}
      if(link){const anchor=document.createElement('a');anchor.href=link.href;anchor.textContent=person.name;entry.append(anchor);}else entry.textContent=person.name;
      value.append(entry);
    });
    else value.textContent=row.value||'';
    node.append(term,value);return node;
  };
  root.querySelectorAll<HTMLElement>('[data-screen-facts]').forEach(list=>{
    const previous=new Map([...list.children].map(node=>[(node as HTMLElement).dataset.detailFact,node as HTMLElement]));
    let anchor:ChildNode|null=list.firstChild;
    for(const row of detailInformationRows(title,pending)){
      let node=previous.get(row.label);previous.delete(row.label);
      // A retry cannot blank an already usable fact.
      if(!node||!(row.pending&&node.dataset.factPending!=='true')&&node.dataset.factSignature!==JSON.stringify(row)){
        const next=rowNode(row);if(node){if(anchor===node)anchor=next;node.replaceWith(next);}node=next;
      }
      if(node!==anchor)list.insertBefore(node,anchor);
      anchor=node.nextSibling;
    }
    previous.forEach(node=>node.remove());
    if(pending)list.setAttribute('aria-busy','true');else list.removeAttribute('aria-busy');
  });
  const year=root.querySelector<HTMLElement>('[data-screen-year]');
  if(year&&title.year&&!year.textContent?.trim()){
    const link=document.createElement('a');link.textContent=title.year;link.href=detailBrowseHref(title.type,{year:title.year})!;
    year.replaceChildren(link);year.hidden=false;
  }
  const genres=root.querySelector<HTMLElement>('[data-screen-genres]');
  if(genres){
    if(title.genres.length&&!genres.children.length)genres.replaceChildren(...title.genres.slice(0,6).map(genre=>{
      const href=detailBrowseHref(title.type,{genre}),node=document.createElement(href?'a':'span');node.textContent=genre;if(href)(node as HTMLAnchorElement).href=href;return node;
    }));
    genres.hidden=!genres.children.length&&!pending;
    if(pending&&!genres.children.length)genres.setAttribute('aria-busy','true');else genres.removeAttribute('aria-busy');
  }
  const synopsis=root.querySelector<HTMLElement>('[data-detail-synopsis]'),text=synopsis?.querySelector<HTMLElement>('[data-biography-text]');
  if(synopsis&&text&&(text.classList.contains('detail-synopsis-skeleton')||synopsis.dataset.screenSynopsisReady!=='true')){
    if(title.overview||!pending){
      unmountContributorBiography(synopsis);
      const copy=detailSynopsis(title.overview);
      text.replaceChildren(document.createTextNode(copy.lead||'No synopsis available yet.'));
      const ellipsis=document.createElement('span'),rest=document.createElement('span');
      ellipsis.dataset.biographyEllipsis='';ellipsis.textContent='…';ellipsis.hidden=!copy.shortened;
      rest.dataset.biographyRest='';rest.textContent=copy.rest;rest.hidden=true;text.append(ellipsis,rest);
      text.classList.remove('detail-synopsis-skeleton');text.removeAttribute('aria-busy');
      synopsis.querySelector('[data-biography-toggle]')?.remove();
      if(copy.expandable){const button=document.createElement('button');button.type='button';button.className='lab-text-button';button.dataset.biographyToggle='';button.textContent='Read more';button.setAttribute('aria-expanded','false');button.setAttribute('aria-controls','detail-synopsis');synopsis.append(button);mountContributorBiography(synopsis,signal);}
      synopsis.dataset.screenSynopsisReady=String(!!title.overview);
    }
  }
  return true;
}
