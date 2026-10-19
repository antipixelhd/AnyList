import {mountDetailImages} from './media-detail-images';
export {bindImageViewerDismissal} from './image-viewer-dismissal';

/** People portraits and studio logos use the same viewer as media galleries. */
export function mountPortraitViewer(root:HTMLElement,signal:AbortSignal) {
  const button=root.querySelector<HTMLButtonElement>('[data-portrait-image]');
  const lightbox=root.querySelector<HTMLDialogElement>('[data-image-viewer]');
  if(!button||!lightbox)return;
  const logo=lightbox.dataset.imageKind==='logo';
  const imagesPath=!logo&&root.dataset.endpoint?`/api/proxy/tracking/${root.dataset.endpoint}/images`:undefined;
  mountDetailImages(root,{imagesAvailable:!!imagesPath,name:button.dataset.portraitCaption,posters:logo?[]:[button.dataset.portraitImage],artwork:logo?[button.dataset.portraitImage]:[]},signal,{lightbox,logo,imagesPath});
}
