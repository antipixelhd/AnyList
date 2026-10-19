import {closeDialog} from './ui-motion';

/** All image viewers close from the backdrop or empty space around the image. */
export function bindImageViewerDismissal(dialog:HTMLDialogElement,signal:AbortSignal) {
  let startedOutside=false;
  const outside=(target:EventTarget|null)=>target instanceof Element&&!target.closest('button,img,[data-image-viewer-track],[data-image-viewer-slots]');
  dialog.addEventListener('pointerdown',event=>{startedOutside=outside(event.target);},{signal});
  dialog.addEventListener('click',event=>{
    if(startedOutside&&outside(event.target))void closeDialog(dialog);
    startedOutside=false;
  },{signal});
  dialog.querySelector('[data-image-viewer-close]')!.addEventListener('click',()=>{void closeDialog(dialog);},{signal});
}

