/** Skip native navigation capture, not just its animation: capture itself can
 * delay the DOM swap on long/mobile pages even with no named elements. */
export function instantPageTransitions(doc:Document) {
  const start=doc.startViewTransition?.bind(doc);
  if(!start)return;
  let navigation:AbortSignal|undefined;
  doc.addEventListener('astro:before-preparation',event=>{navigation=(event as Event&{signal:AbortSignal}).signal;});
  doc.addEventListener('astro:after-swap',()=>{navigation=undefined;});
  doc.startViewTransition=update=>{
    const transition=start(update);
    // skipTransition still runs the router's update callback and lifecycle.
    if(navigation&&!navigation.aborted){
      // Intentional skipping rejects ready; Astro only awaits updateCallbackDone.
      void transition.ready.catch(()=>{});
      transition.skipTransition();
    }
    return transition;
  };
}
