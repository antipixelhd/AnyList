/** One private request, independently delivered shelves in stable page slots. */
export async function readBrowseSections<T>(path:string, signal:AbortSignal,
  paint:(index:number, section:T, notice?:string)=>void, fetcher:typeof fetch=fetch) {
  const response=await fetcher(`/api/proxy/tracking/${path}&stream=true`,{signal});
  if(!response.ok||!response.body)throw new Error('Unable to load titles. Try again.');
  const reader=response.body.getReader(), decoder=new TextDecoder();
  const sections=new Map<number,T>();
  let pending='', notice:string|undefined;
  const consume=(line:string)=>{
    if(!line.trim())return;
    if(signal.aborted)throw new DOMException('Aborted','AbortError');
    const update=JSON.parse(line);
    if(update.error)throw new Error(update.error);
    if(!Number.isInteger(update.index)||update.index<0||update.index>3||!update.section||sections.has(update.index))throw new Error('Invalid Browse response.');
    sections.set(update.index,update.section);
    notice ||= update.notice;
    paint(update.index,update.section,update.notice);
  };
  try{
    while(true){
      const {done,value}=await reader.read();
      pending+=decoder.decode(value,{stream:!done});
      let end:number;
      while((end=pending.indexOf('\n'))>=0){consume(pending.slice(0,end));pending=pending.slice(end+1);}
      if(pending.length>2_000_000)throw new Error('Invalid Browse response.');
      if(done)break;
    }
    consume(pending);
    if(sections.size!==4)throw new Error('Incomplete Browse response. Try again.');
    return {sections:[0,1,2,3].map(index=>sections.get(index)!),notice};
  }finally{
    await reader.cancel().catch(()=>{});
    reader.releaseLock();
  }
}
