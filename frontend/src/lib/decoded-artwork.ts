type Pending={src:string;resolve:()=>void;promise:Promise<void>;priority:boolean;image?:HTMLImageElement;promote?:()=>void};
/** Browser HTTP caching owns files; this bounded LRU retains decoded elements. */
export class DecodedArtwork {
  private images=new Map<string,HTMLImageElement>();
  private pending=new Map<string,Pending>();
  private queue:Pending[]=[];
  private active=0;
  private generation=0;
  private limit:number;
  private pixelLimit:number;
  private ready:()=>void;
  constructor(limit=32,pixelLimit=12_000_000,ready=()=>{}) {this.limit=limit;this.pixelLimit=pixelLimit;this.ready=ready;}
  clear() {
    this.generation++;this.images.clear();
    this.queue.splice(0).forEach(item=>item.resolve());this.pending.clear();
  }
  get(src:string|undefined) {
    if(!src)return;
    const image=this.images.get(src);
    if(!image)return;
    this.images.delete(src);this.images.set(src,image);
    return image;
  }
  warm(src:string,priority=false) {
    if(this.get(src)||typeof Image==='undefined')return Promise.resolve();
    const existing=this.pending.get(src);
    if(existing){
      if(priority){if(!existing.priority)existing.promote?.();existing.priority=true;if(existing.image)existing.image.fetchPriority='high';
        const index=this.queue.indexOf(existing);if(index>=0){this.queue.splice(index,1);this.queue.unshift(existing);}this.drain();}
      return existing.promise;
    }
    let resolve!:()=>void;
    const promise=new Promise<void>(done=>{resolve=done;});
    const item:Pending={src,promise,resolve,priority};this.pending.set(src,item);
    if(priority)this.queue.unshift(item);else this.queue.push(item);
    this.drain();return promise;
  }
  private drain(){
    // Keep the two speculative lanes; reserve one extra lane for direct intent.
    while(this.queue.length&&this.active<(this.queue[0].priority?3:2)){
      const item=this.queue.shift()!,generation=this.generation;
      const image=new Image();item.image=image;image.decoding='async';image.fetchPriority=item.priority?'high':'low';
      this.active++;image.src=item.src;
      let timer:ReturnType<typeof setTimeout>|undefined;
      const timeout=new Promise<boolean>(resolve=>{
        const cancel=()=>{image.removeAttribute('src');resolve(false);};
        item.promote=()=>{clearTimeout(timer);timer=setTimeout(cancel,30000);};
        timer=setTimeout(cancel,item.priority?30000:8000);
      });
      void Promise.race([image.decode().then(()=>true),timeout]).then(ready=>{
        if(!ready||generation!==this.generation)return;
        this.images.set(item.src,image);
        let pixels=[...this.images.values()].reduce((sum,image)=>sum+(image.naturalWidth||0)*(image.naturalHeight||0),0);
        for(const [key,entry] of this.images){
          if(this.images.size<=this.limit&&(pixels<=this.pixelLimit||this.images.size===1))break;
          pixels-=(entry.naturalWidth||0)*(entry.naturalHeight||0);this.images.delete(key);
        }
        this.ready();
      }).catch(()=>{}).finally(()=>{
        clearTimeout(timer);
        if(this.pending.get(item.src)===item)this.pending.delete(item.src);
        this.active--;item.resolve();this.drain();
      });
    }
  }
}
