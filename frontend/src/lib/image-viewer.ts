import {mountDetailImages} from './media-detail-images';
export {bindImageViewerDismissal} from './image-viewer-dismissal';

/** Single portraits and studio logos use the same viewer as media galleries. */
export function mountPortraitViewer(root:HTMLElement,signal:AbortSignal) {
  const button=root.querySelector<HTMLButtonElement>('[data-portrait-image]');
  const lightbox=root.querySelector<HTMLDialogElement>('[data-image-viewer]');
  if(!button||!lightbox)return;
  const logo=lightbox.dataset.imageKind==='logo';
  mountDetailImages(root,{name:button.dataset.portraitCaption,posters:logo?[]:[button.dataset.portraitImage],artwork:logo?[button.dataset.portraitImage]:[]},signal,{lightbox,logo});
}
