import {detailBrowseHref,detailFacts,detailSynopsis,prepareDetail} from './media-detail-data';
import {mountContributorBiography,unmountContributorBiography} from './contributor-biography';

/** Publish public metadata into owned slots; never re-seed editor/private state. */
export function publishGamePrimary(root:HTMLElement,data:Record<string,any>,id:number,signal:AbortSignal) {
  if(signal.aborted||data.id!==id||data.type!=='game')return false;
  const title=prepareDetail(data);
  const heading=root.querySelector('.lab-heading h1');if(heading)heading.textContent=title.title;
  const year=root.querySelector<HTMLElement>('[data-game-year]');
  if(year){
    year.hidden=!title.year;year.replaceChildren();
    if(title.year){const link=document.createElement('a');link.textContent=title.year;link.href=detailBrowseHref('game',{year:title.year})||'/browse/games';year.append(link);}
  }
  const genres=root.querySelector<HTMLElement>('[data-game-genres]');
  if(genres){
    genres.replaceChildren(...title.genres.slice(0,6).map(genre=>{
      const node=document.createElement('a');node.textContent=genre;
      node.href=detailBrowseHref('game',{genre})||'/browse/games';return node;
    }));genres.hidden=!title.genres.length;
  }
  const synopsis=root.querySelector<HTMLElement>('[data-detail-synopsis]');
  if(synopsis){
    const text=synopsis.querySelector<HTMLElement>('[data-biography-text]')!;
    const copy=detailSynopsis(title.overview,true);
    const previous=(text.firstChild?.textContent||'')+(text.querySelector('[data-biography-rest]')?.textContent||'');
    if(previous.trim()!==(copy.lead+copy.rest).trim()||text.classList.contains('detail-synopsis-skeleton')){
      const expanded=synopsis.querySelector('[data-biography-toggle]')?.getAttribute('aria-expanded')==='true';
      unmountContributorBiography(synopsis);
      text.replaceChildren(document.createTextNode(copy.lead||'No synopsis available yet.'));
      const ellipsis=document.createElement('span');ellipsis.dataset.biographyEllipsis='';ellipsis.hidden=true;
      const rest=document.createElement('span');rest.dataset.biographyRest='';rest.hidden=true;rest.textContent=copy.rest;
      text.append(ellipsis,rest);text.classList.remove('detail-synopsis-skeleton');text.removeAttribute('aria-busy');
      synopsis.querySelector('[data-biography-toggle]')?.remove();
      if(copy.expandable){
        const button=document.createElement('button');button.type='button';button.className='lab-text-button';
        button.dataset.biographyToggle='';button.textContent='Read more';button.setAttribute('aria-expanded','false');button.setAttribute('aria-controls','detail-synopsis');
        synopsis.append(button);mountContributorBiography(synopsis,signal,expanded);
      }
    }
  }
  root.querySelectorAll<HTMLElement>('.lab-facts dl,.lab-inline-facts dl').forEach(list=>{
    detailFacts(title).forEach(fact=>{
      const row=document.createElement('div'),term=document.createElement('dt'),value=document.createElement('dd');
      term.textContent=fact.label;value.textContent=fact.value;row.append(term,value);
      const old=[...list.children].find(child=>child.querySelector('dt')?.textContent===fact.label);
      if(old)old.replaceWith(row);else list.append(row);
    });
  });
  const cover=root.querySelector<HTMLElement>('.media-cover-empty');
  if(cover&&title.poster){
    const button=document.createElement('button');button.type='button';button.className='lab-hero-poster lab-poster-button media-cover detail-image-skeleton';
    button.dataset.detailGallery='posters';button.setAttribute('aria-label',`Expand ${title.title} cover`);
    button.dataset.detailCaption=`${title.title} · Cover`;
    const image=document.createElement('img');image.className='lab-poster';image.alt=`${title.title} cover`;image.fetchPriority='high';image.style.opacity='0';
    image.addEventListener('load',()=>{button.classList.remove('detail-image-skeleton');image.style.removeProperty('opacity');},{once:true,signal});
    image.addEventListener('error',()=>button.replaceWith(cover),{once:true,signal});
    button.append(image);cover.replaceWith(button);
  }
  // The existing image controller owns decoding, selected art, responsive URLs,
  // modal state and deferred gallery loading; this does not warm gallery extras.
  root.dispatchEvent(new CustomEvent('anylist:game-primary',{detail:data}));
  return true;
}
