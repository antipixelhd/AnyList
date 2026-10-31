/** Two /dev treatments of the same optional extension to the normal finish date. */
export function setupFinishDateDisclosure(dialog: HTMLDialogElement, signal: AbortSignal) {
  const candidate=dialog.querySelector<HTMLElement>('[data-finish-field]');
  if (!candidate) return {reset(){},reveal(){}};
  const field:HTMLElement=candidate;
  const trigger=field.querySelector<HTMLButtonElement>('[data-finish-trigger]')!;
  const panel=field.querySelector<HTMLElement>('[data-finish-panel]')!;
  const floating=field.dataset.finishField==='popover';
  const finePointer=matchMedia('(hover:hover) and (pointer:fine)');
  let opened=false, pinned=false, hovering=false;
  let openTimer: ReturnType<typeof setTimeout> | undefined;
  let closeTimer: ReturnType<typeof setTimeout> | undefined;
  let anchored=false;

  function clearTimers() {clearTimeout(openTimer);clearTimeout(closeTimer);}
  function position() {
    if (!opened || !floating) return;
    const rect=field.querySelector<HTMLElement>('.calendar-input')!.getBoundingClientRect();
    const viewport=window.visualViewport;
    const left=viewport?.offsetLeft||0, top=viewport?.offsetTop||0;
    const width=viewport?.width||innerWidth, height=viewport?.height||innerHeight;
    panel.style.maxHeight=`${Math.max(100,height-24)}px`;
    panel.style.left=`${Math.max(left+12,Math.min(rect.right-panel.offsetWidth,left+width-panel.offsetWidth-12))}px`;
    const below=rect.bottom+8;
    panel.style.top=`${Math.max(top+12,Math.min(below+panel.offsetHeight<=top+height-12?below:rect.top-panel.offsetHeight-8,top+height-panel.offsetHeight-12))}px`;
  }
  function open(pin=false, focus=false) {
    clearTimers();
    if (trigger.hidden || !dialog.open || signal.aborted) return;
    pinned ||= pin;
    if (!opened) {
      // Growing a centered modal would move the hover target out from under the
      // pointer and repeatedly close/reopen it. Keep its measured top stable.
      if (!floating && finePointer.matches && !anchored) {
        const rect=dialog.getBoundingClientRect();
        const maximum=parseFloat(getComputedStyle(dialog).maxHeight)||innerHeight;
        dialog.style.marginTop=`${rect.top}px`;dialog.style.marginBottom='auto';
        dialog.style.maxHeight=`${Math.min(maximum,innerHeight-rect.top-12)}px`;
        anchored=true;
      }
      opened=true;
      panel.inert=false;panel.setAttribute('aria-hidden','false');
      field.dataset.finishOpen='true';trigger.setAttribute('aria-expanded','true');
      if (floating) panel.showPopover();
    }
    position();
    if (focus) panel.querySelector<HTMLInputElement>('[data-date-display]')?.focus({preventScroll:true});
  }
  function close(returnFocus=false) {
    clearTimers();opened=false;pinned=false;hovering=false;
    field.dataset.finishOpen='false';trigger.setAttribute('aria-expanded','false');
    panel.inert=true;panel.setAttribute('aria-hidden','true');
    if (floating && panel.matches(':popover-open')) panel.hidePopover();
    if (returnFocus) trigger.focus({preventScroll:true});
  }
  function reset() {
    close();anchored=false;
    if (!floating) {
      dialog.style.removeProperty('margin-top');dialog.style.removeProperty('margin-bottom');dialog.style.removeProperty('max-height');
    }
  }
  function scheduleClose() {
    clearTimeout(closeTimer);
    closeTimer=setTimeout(()=>{
      if (!pinned && !hovering && !panel.contains(document.activeElement) && !panel.querySelector('.calendar-popover:popover-open')) close();
    },180);
  }
  trigger.addEventListener('pointerenter',()=>{
    if (!finePointer.matches) return;
    hovering=true;clearTimeout(closeTimer);
    openTimer=setTimeout(()=>open(),160);
  },{signal});
  trigger.addEventListener('pointerleave',()=>{hovering=false;clearTimeout(openTimer);scheduleClose();},{signal});
  panel.addEventListener('pointerenter',()=>{hovering=true;clearTimeout(closeTimer);},{signal});
  panel.addEventListener('pointerleave',()=>{hovering=false;scheduleClose();},{signal});
  panel.addEventListener('focusin',()=>{clearTimeout(closeTimer);pinned=true;},{signal});
  panel.addEventListener('focusout',scheduleClose,{signal});
  trigger.addEventListener('click',()=>{if (opened && pinned) close();else open(true);},{signal});
  trigger.addEventListener('keydown',event=>{
    if (event.key==='ArrowDown') {event.preventDefault();open(true,true);}
  },{signal});
  panel.querySelector('[data-finish-close]')!.addEventListener('click',()=>close(true),{signal});
  document.addEventListener('pointerdown',event=>{
    if (opened && !panel.contains(event.target as Node) && !trigger.contains(event.target as Node)) close();
  },{signal});
  // The shared calendar handles the first Escape; a second closes this extension.
  dialog.addEventListener('keydown',event=>{
    if (opened && event.key==='Escape' && !event.defaultPrevented) {event.preventDefault();event.stopPropagation();close(true);}
  },{signal});
  dialog.addEventListener('close',()=>close(),{signal});
  document.addEventListener('scroll',position,{signal,capture:true});
  window.addEventListener('resize',position,{signal});
  window.visualViewport?.addEventListener('resize',position,{signal});
  const observer=new ResizeObserver(position);observer.observe(panel);
  signal.addEventListener('abort',()=>{reset();observer.disconnect();},{once:true});
  return {reset,reveal:()=>open(true)};
}
