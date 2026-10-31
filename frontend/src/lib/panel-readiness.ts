/** Install a fetched route's CSS before exposing its DOM, in dev and production. */
export async function preparePanelStyles(source:Document,base:string,signal:AbortSignal,document:Document=globalThis.document){
  if(signal.aborted)throw new DOMException('Aborted','AbortError');
  for(const style of source.querySelectorAll<HTMLStyleElement>('style')){
    const identity=style.getAttribute('data-vite-dev-id');
    const existing=[...document.head.querySelectorAll<HTMLStyleElement>('style')].find(item=>identity?item.getAttribute('data-vite-dev-id')===identity:item.textContent===style.textContent);
    // The active runtime (including Vite HMR) owns installed style identities.
    // A fetched HTML snapshot must not replace their live CSS or undo updates.
    if(!existing)document.head.append(document.importNode(style,true));
  }
  await Promise.all([...source.querySelectorAll<HTMLLinkElement>('link[rel=stylesheet][href]')].map(async link=>{
    const href=new URL(link.getAttribute('href')!,base).href;
    let css=[...document.head.querySelectorAll<HTMLLinkElement>('link[rel=stylesheet]')].find(item=>item.href===href);
    if(css?.sheet)return;
    const added=!css;
    if(!css){css=document.importNode(link,true);css.href=href;}
    const target=css;
    await new Promise<void>((resolve,reject)=>{
      const cleanup=()=>{clearTimeout(timeout);target.removeEventListener('load',loaded);target.removeEventListener('error',failed);signal.removeEventListener('abort',aborted);};
      const loaded=()=>{cleanup();resolve();};
      const failed=()=>{cleanup();if(added)target.remove();reject(new Error('Styles unavailable'));};
      const aborted=()=>{cleanup();if(added)target.remove();reject(new DOMException('Aborted','AbortError'));};
      const timeout=setTimeout(failed,15000);
      target.addEventListener('load',loaded,{once:true});target.addEventListener('error',failed,{once:true});signal.addEventListener('abort',aborted,{once:true});
      if(signal.aborted){aborted();return;}
      if(added)document.head.append(target);
      if(target.sheet)loaded();
    });
  }));
  if(signal.aborted)throw new DOMException('Aborted','AbortError');
}

/** Let connected, invisible panels finish measurement without exposing an intermediate frame. */
export function panelLayoutFrame(signal:AbortSignal){
  return new Promise<void>((resolve,reject)=>{
    const frame=requestAnimationFrame(()=>{signal.removeEventListener('abort',abort);resolve();});
    const abort=()=>{cancelAnimationFrame(frame);signal.removeEventListener('abort',abort);reject(new DOMException('Aborted','AbortError'));};
    signal.addEventListener('abort',abort,{once:true});if(signal.aborted)abort();
  });
}
