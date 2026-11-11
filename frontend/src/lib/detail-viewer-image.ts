import { scheduleArtworkPulse } from './loading-reveal.ts';

type ImageRequest = {
  url: string;
  pending: Promise<boolean>;
  previews: Set<HTMLImageElement>;
  previewWidth: number;
  finishPulse?: ReturnType<typeof scheduleArtworkPulse>;
};

/** Decode on the real slide, retaining in-flight/ready images across selection.
 * Starting another slide must never cancel one the user has already exposed. */
export function createDetailViewerImageLoader(signal:AbortSignal) {
  const requests=new WeakMap<HTMLImageElement,ImageRequest>();
  return (image:HTMLImageElement,skeleton:HTMLElement,url:string,alt:string,priority:'high'|'low'='high',preview?:HTMLImageElement|HTMLImageElement[])=>{
    if(signal.aborted)return Promise.resolve(false);
    image.alt=alt;
    const existing=requests.get(image);
    if(priority==='high')image.fetchPriority=priority;
    const showPreview=(candidate:HTMLImageElement)=>{
      const request=requests.get(image);
      if(signal.aborted||skeleton.hidden||!candidate.complete||!candidate.naturalWidth||request?.url!==url||candidate.naturalWidth<request.previewWidth)return;
      request.previewWidth=candidate.naturalWidth;
      skeleton.style.backgroundImage=`url(${JSON.stringify(candidate.currentSrc||candidate.src)})`;
      skeleton.dataset.viewerPreview='';
      skeleton.setAttribute('role','img');skeleton.setAttribute('aria-label',alt);
      request.finishPulse?.(true);
    };
    const watchPreview=(request:{previews:Set<HTMLImageElement>})=>{
      if(!preview)return;
      for(const candidate of Array.isArray(preview)?preview:[preview]){
        showPreview(candidate);
        if(!candidate.complete&&!request.previews.has(candidate)){
          request.previews.add(candidate);
          candidate.addEventListener('load',()=>showPreview(candidate),{once:true,signal});
        }
      }
    };
    const cue = (request: ImageRequest) => {
      if(priority==='high'&&!skeleton.hidden&&!('viewerPreview' in skeleton.dataset)&&!request.finishPulse&&image.parentElement)
        request.finishPulse=scheduleArtworkPulse(image.parentElement,signal);
    };
    if(existing?.url===url){watchPreview(existing);cue(existing);return existing.pending;}
    if(existing){
      existing.finishPulse?.();
      delete skeleton.dataset.viewerPreview;skeleton.style.backgroundImage='';
      skeleton.setAttribute('role','status');skeleton.setAttribute('aria-label','Loading image');
    }
    image.fetchPriority=priority;
    image.src=url;
    if(image.complete&&image.naturalWidth>0){
      image.style.visibility='';skeleton.hidden=true;
      const pending=Promise.resolve(true);requests.set(image,{url,pending,previews:new Set(),previewWidth:0});return pending;
    }
    image.style.visibility='hidden';skeleton.hidden=false;
    const pending=image.decode().then(()=>{
      if(signal.aborted||requests.get(image)?.pending!==pending)return false;
      image.style.visibility='';skeleton.hidden=true;
      requests.get(image)?.finishPulse?.(true);
      delete skeleton.dataset.viewerPreview;skeleton.style.backgroundImage='';
      return true;
    }).catch(error=>{
      if(signal.aborted||requests.get(image)?.pending!==pending)return false;
      requests.get(image)?.finishPulse?.();
      throw error;
    });
    const request:ImageRequest={url,pending,previews:new Set<HTMLImageElement>(),previewWidth:0};
    requests.set(image,request);watchPreview(request);cue(request);return pending;
  };
}
