export type GameVerificationPage={verification_pending?:boolean;verification_error?:string;retry_after?:number};
export const gameBrowseCacheable=(page:GameVerificationPage)=>!page.verification_pending&&!page.verification_error;

export function pauseGameVerification(ms:number,signal:AbortSignal){
  if(signal.aborted)return Promise.reject(new DOMException('Aborted','AbortError'));
  return new Promise<void>((resolve,reject)=>{
    const cleanup=()=>{clearTimeout(timer);signal.removeEventListener('abort',abort);};
    const abort=()=>{cleanup();reject(new DOMException('Aborted','AbortError'));};
    const timer=setTimeout(()=>{cleanup();resolve();},ms);
    signal.addEventListener('abort',abort,{once:true});
  });
}

/** Publish only this query's verified prefix; a timeout is never an empty result. */
export async function readVerifiedGamePage<T extends GameVerificationPage>(read:()=>Promise<T>,
  update:(page:T)=>void,signal:AbortSignal,pause=pauseGameVerification,attempts=35){
  for(let attempt=0;attempt<attempts;attempt++){
    if(signal.aborted)throw new DOMException('Aborted','AbortError');
    const page=await read();
    if(signal.aborted)throw new DOMException('Aborted','AbortError');
    update(page);
    if(page.verification_error)throw new Error('Unable to verify game scores. Try again.');
    if(!page.verification_pending)return page;
    if(attempt+1<attempts)await pause(Math.min(5,Math.max(1,page.retry_after||2))*1000,signal);
  }
  throw new Error('Game scores are still being checked. Try again.');
}
