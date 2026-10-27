const mounts=new WeakMap<HTMLElement,AbortController>();
export function unmountContributorBiography(root:HTMLElement){mounts.get(root)?.abort();}

export function mountContributorBiography(root: HTMLElement, parentSignal: AbortSignal, initialExpanded=false) {
  unmountContributorBiography(root);
  if(parentSignal.aborted)return;
  const button = root.querySelector<HTMLButtonElement>('[data-biography-toggle]');
  const text = root.querySelector<HTMLElement>('[data-biography-text]');
  const rest = root.querySelector<HTMLElement>('[data-biography-rest]');
  const ellipsis = root.querySelector<HTMLElement>('[data-biography-ellipsis]');
  if (!button || !text || !rest || !ellipsis) return;
  const controller=new AbortController(),signal=controller.signal;
  mounts.set(root,controller);
  const dispose=()=>controller.abort();parentSignal.addEventListener('abort',dispose,{once:true});
  signal.addEventListener('abort',()=>{parentSignal.removeEventListener('abort',dispose);if(mounts.get(root)===controller)mounts.delete(root);},{once:true});
  const shortened = !ellipsis.hidden;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  let animation: Animation | undefined;
  let expanded = initialExpanded;
  const settle = () => {
    animation?.cancel(); animation = undefined;
    rest.hidden = !expanded; ellipsis.hidden = expanded || !shortened;
    text.style.removeProperty('overflow');
  };
  button.setAttribute('aria-expanded',String(expanded));button.textContent=expanded?'Read less':'Read more';settle();
  button.addEventListener('click', () => {
    const start = text.getBoundingClientRect().height;
    animation?.cancel();
    expanded = !expanded;
    if (!expanded && root.getBoundingClientRect().top < 0) {
      root.scrollIntoView({block:'start',behavior:reduced.matches?'auto':'smooth'});
    }
    button.setAttribute('aria-expanded', String(expanded));
    button.textContent = expanded ? 'Read less' : 'Read more';
    rest.hidden = !expanded; ellipsis.hidden = expanded || !shortened;
    const end = text.getBoundingClientRect().height;
    if (reduced.matches || start === end) { settle(); return; }
    // Keep the full paragraph flowing in place while clipping its animated height.
    rest.hidden = false; ellipsis.hidden = true;
    text.style.overflow = 'hidden';
    const current = text.animate([{height:`${start}px`}, {height:`${end}px`}], {
      duration:320, easing:'cubic-bezier(.2,.7,.2,1)', fill:'both',
    });
    animation = current;
    current.finished.then(() => { if (animation === current) settle(); }).catch(() => {});
  }, {signal});
  reduced.addEventListener('change', settle, {signal});
  signal.addEventListener('abort', settle, {once:true});
}
