/** One cancellable metadata read; a failed read can retry without resetting filters. */
export function createBrowseFacetLoader<T>(read:(signal:AbortSignal)=>Promise<T>,apply:(data:T)=>void,
  busy:(pending:boolean)=>void,error:(failed:boolean)=>void,signal:AbortSignal){
  let pending:Promise<void>|undefined;
  return {load:():Promise<void>=>{
    if(signal.aborted)return Promise.resolve();
    if(pending)return pending;
    busy(true);error(false);
    pending=Promise.resolve().then(()=>read(signal)).then(data=>{
      if(!signal.aborted)apply(data);
    }).catch(()=>{if(!signal.aborted)error(true);}).finally(()=>{
      pending=undefined;if(!signal.aborted)busy(false);
    });
    return pending;
  }};
}
