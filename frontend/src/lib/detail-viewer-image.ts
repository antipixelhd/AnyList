/** Decode on the real slide, retaining in-flight/ready images across selection.
 * Starting another slide must never cancel one the user has already exposed. */
export function createDetailViewerImageLoader(signal:AbortSignal) {
  const requests=new WeakMap<HTMLImageElement,{url:string;pending:Promise<boolean>}>();
  return (image:HTMLImageElement,skeleton:HTMLElement,url:string,alt:string)=>{
    if(signal.aborted)return Promise.resolve(false);
    image.alt=alt;
    const existing=requests.get(image);
    if(existing?.url===url)return existing.pending;
    image.fetchPriority='high';
    image.src=url;
    if(image.complete&&image.naturalWidth>0){
      image.style.visibility='';skeleton.hidden=true;
      const pending=Promise.resolve(true);requests.set(image,{url,pending});return pending;
    }
    image.style.visibility='hidden';skeleton.hidden=false;
    const pending=image.decode().then(()=>{
      if(signal.aborted||requests.get(image)?.pending!==pending)return false;
      image.style.visibility='';skeleton.hidden=true;return true;
    }).catch(error=>{
      if(signal.aborted||requests.get(image)?.pending!==pending)return false;
      throw error;
    });
    requests.set(image,{url,pending});return pending;
  };
}
