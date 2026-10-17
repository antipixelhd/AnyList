/** Publish only the hero actually displayed, so later editor intent reuses it. */
export function commitDetailHero(hero:HTMLImageElement,candidate:HTMLImageElement,url:string,
  button:HTMLButtonElement|null,committed:(url:string)=>void) {
  if(candidate!==hero){candidate.className=hero.className;candidate.alt='';hero.replaceWith(candidate);}
  candidate.hidden=false;
  if(button){button.dataset.detailImage=url;button.hidden=false;}
  committed(url);
}
