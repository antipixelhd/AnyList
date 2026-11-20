/** Keep a displayed loading state readable; cached content never starts the clock. */
export function createMinimumDisplay(signal:AbortSignal,minimumMs=350,now=()=>performance.now()){
  let shownAt:number|undefined,timer:ReturnType<typeof setTimeout>|undefined;
  const cancel=()=>{clearTimeout(timer);timer=undefined;};
  signal.addEventListener('abort',cancel,{once:true});
  return {
    show(){shownAt??=now();},
    cancel,
    finish(apply:()=>void){
      cancel();
      const remaining=shownAt===undefined?0:Math.max(0,minimumMs-(now()-shownAt));
      const complete=()=>{timer=undefined;shownAt=undefined;if(!signal.aborted)apply();};
      if(remaining)timer=setTimeout(complete,remaining);else complete();
    },
    settle(){
      return new Promise<void>(resolve=>{
        const done=()=>{signal.removeEventListener('abort',done);resolve();};
        if(signal.aborted){done();return;}
        signal.addEventListener('abort',done,{once:true});this.finish(done);
      });
    },
    reset(){cancel();shownAt=undefined;},
  };
}
