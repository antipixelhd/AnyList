/** Fit the side panel within the viewport or the preview canvas. */
export function placeProgressCardDetails(card:HTMLElement,leftEdge=20,rightEdge=document.documentElement.clientWidth-20){
  const details=card.querySelector<HTMLElement>('.progress-card-details');
  if(!details)return;
  details.style.removeProperty('--progress-card-side-space');
  const {left,right}=card.getBoundingClientRect();
  const gap=16;
  const rightSpace=rightEdge-right-gap;
  const leftSpace=left-leftEdge-gap;
  const flip=rightSpace<details.offsetWidth&&leftSpace>rightSpace;
  card.classList.toggle('details-flipped',flip);
  details.style.setProperty('--progress-card-side-space',`${Math.max(0,flip?leftSpace:rightSpace)}px`);
}
