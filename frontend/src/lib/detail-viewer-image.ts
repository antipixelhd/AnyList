/** Decode on the real slide, retaining in-flight/ready images across selection.
 * Starting another slide must never cancel one the user has already exposed. */
export function createDetailViewerImageLoader(signal:AbortSignal) {
  const requests=new WeakMap<HTMLImageElement,{url:string;pending:Promise<boolean>;previews:Set<HTMLImageElement>}>();
  return (image:HTMLImageElement,skeleton:HTMLElement,url:string,alt:string,priority:'high'|'low'='high',preview?:HTMLImageElement)=>{
    if(signal.aborted)return Promise.resolve(false);
    image.alt=alt;
    const existing=requests.get(image);
    if(priority==='high')image.fetchPriority=priority;
    const showPreview=()=>{
      if(signal.aborted||skeleton.hidden||!preview?.complete||!preview.naturalWidth||requests.get(image)?.url!==url)return;
      skeleton.style.backgroundImage=`url(${JSON.stringify(preview.currentSrc||preview.src)})`;
      skeleton.dataset.viewerPreview='';
      skeleton.setAttribute('role','img');skeleton.setAttribute('aria-label',alt);
    };
    const watchPreview=(request:{previews:Set<HTMLImageElement>})=>{
      if(!preview)return;
      showPreview();
      if(!preview.complete&&!request.previews.has(preview)){
        request.previews.add(preview);
        preview.addEventListener('load',showPreview,{once:true,signal});
      }
    };
    if(existing?.url===url){watchPreview(existing);return existing.pending;}
    if(existing){
      delete skeleton.dataset.viewerPreview;skeleton.style.backgroundImage='';
      skeleton.setAttribute('role','status');skeleton.setAttribute('aria-label','Loading image');
    }
    image.fetchPriority=priority;
    image.src=url;
    if(image.complete&&image.naturalWidth>0){
      image.style.visibility='';skeleton.hidden=true;
      const pending=Promise.resolve(true);requests.set(image,{url,pending,previews:new Set()});return pending;
    }
    image.style.visibility='hidden';skeleton.hidden=false;
    const pending=image.decode().then(()=>{
      if(signal.aborted||requests.get(image)?.pending!==pending)return false;
      image.style.visibility='';skeleton.hidden=true;
      delete skeleton.dataset.viewerPreview;skeleton.style.backgroundImage='';
      return true;
    }).catch(error=>{
      if(signal.aborted||requests.get(image)?.pending!==pending)return false;
      throw error;
    });
    const request={url,pending,previews:new Set<HTMLImageElement>()};
    requests.set(image,request);watchPreview(request);return pending;
  };
}
