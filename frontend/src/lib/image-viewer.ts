import {closeDialog,showDialog,reveal} from './ui-motion';
import {showToast} from './toasts';

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

export function mountPortraitViewer(root:HTMLElement,signal:AbortSignal) {
  const button=root.querySelector<HTMLButtonElement>('[data-portrait-image]');
  const dialog=root.querySelector<HTMLDialogElement>('[data-image-viewer]');
  if(!button||!dialog)return;
  bindImageViewerDismissal(dialog,signal);
  const image=dialog.querySelector<HTMLImageElement>('[data-image-viewer-image]')!;
  const caption=dialog.querySelector<HTMLElement>('p')!;
  let revision=0;
  button.addEventListener('click',()=>{
    if(button.disabled)return;
    const version=++revision;
    image.src=button.dataset.portraitImage!;
    image.alt=button.dataset.portraitCaption||'';caption.textContent=image.alt;
    showDialog(dialog);
    void image.decode().then(()=>{
      if(version===revision&&!signal.aborted&&dialog.open)reveal(image);
    }).catch(()=>{
      if(version!==revision||signal.aborted)return;
      void closeDialog(dialog);showToast('Unable to load this image.','error');
    });
  },{signal});
}
