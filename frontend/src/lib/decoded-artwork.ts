/** Small viewer-owned LRU of actual decoded elements, not historical ready flags. */
export class DecodedArtwork {
  private images=new Map<string,{image:HTMLImageElement;ready:boolean;pending:Promise<void>}>();
  private limit:number;
  constructor(limit=12) { this.limit=limit; }
  clear() { this.images.clear(); }
  get(src:string|undefined) {
    if(!src)return;
    const item=this.images.get(src);
    if(!item?.ready)return;
    this.images.delete(src);this.images.set(src,item);
    return item.image;
  }
  warm(src:string) {
    const existing=this.images.get(src);
    if(existing)return existing.pending;
    if(typeof Image==='undefined')return Promise.resolve();
    const image=new Image();
    image.decoding='async';image.fetchPriority='low';
    const item={image,ready:false,pending:Promise.resolve()};
    this.images.set(src,item);
    while(this.images.size>this.limit)this.images.delete(this.images.keys().next().value!);
    image.src=src;
    item.pending=image.decode().then(()=>{
      item.ready=true;
      let pixels=[...this.images.values()].reduce((sum,item)=>sum+(item.ready?(item.image.naturalWidth||0)*(item.image.naturalHeight||0):0),0);
      for(const [key,entry] of this.images){
        if(pixels<=12_000_000||this.images.size<=1)break;
        if(!entry.ready)continue;
        pixels-=(entry.image.naturalWidth||0)*(entry.image.naturalHeight||0);this.images.delete(key);
      }
    }).catch(()=>{
      if(this.images.get(src)===item)this.images.delete(src);
    });
    return item.pending;
  }
}
