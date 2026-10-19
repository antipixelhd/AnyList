/** M3 standard spatial stiffness; critical damping is a deliberate no-bounce
 * customization for bounded photos. Units: seconds, CSS pixels, scale ratios.
 * https://m3.material.io/styles/motion/overview/how-it-works
 * https://github.com/androidx/androidx/blob/androidx-main/compose/material3/material3/src/commonMain/kotlin/androidx/compose/material3/tokens/StandardMotionTokens.kt
 */
export const gallerySpatial={default:700,fast:1400};
type Numbers=Record<string,number>;
type Bounds<T>=Partial<Record<keyof T,{min:number;max:number}>>;
type SpringOptions<T>={stiffness?:number;precision?:Partial<T>;bounds?:Bounds<T>|(()=>Bounds<T>)};
export function createGallerySpring<T extends Numbers>(initial:T,paint:(value:T)=>void,signal:AbortSignal,options:SpringOptions<T>={}) {
  const keys=Object.keys(initial) as (keyof T)[],omega=Math.sqrt(options.stiffness||gallerySpatial.default);
  let value={...initial},goal={...initial},velocity=Object.fromEntries(keys.map(key=>[key,0])) as T;
  let frame=0,active=false,time=performance.now(),dragTime:number|undefined,complete:(()=>void)|undefined;
  const zero=()=>{for(const key of keys)velocity[key]=0 as T[keyof T];};
  const constrain=()=>{
    const bounds=typeof options.bounds==='function'?options.bounds():options.bounds;
    for(const key of keys){
      const bound=bounds?.[key];if(!bound)continue;
      goal[key]=Math.max(bound.min,Math.min(bound.max,goal[key])) as T[keyof T];
      const clamped=Math.max(bound.min,Math.min(bound.max,value[key]));
      if(clamped!==value[key]){value[key]=clamped as T[keyof T];velocity[key]=0 as T[keyof T];}
    }
  };
  const resting=()=>keys.every(key=>{
    const precision=options.precision?.[key]??.1;
    return Math.abs(value[key]-goal[key])<=precision&&Math.abs(velocity[key])<=precision*omega;
  });
  const advance=(now:number)=>{
    if(!active)return;
    const stamp=Math.max(time,now),dt=(stamp-time)/1000,decay=Math.exp(-omega*dt);time=stamp;
    // Closed-form critical damping avoids numerical drift across frame rates.
    for(const key of keys){
      const displacement=value[key]-goal[key],c=velocity[key]+omega*displacement;
      value[key]=(goal[key]+(displacement+c*dt)*decay) as T[keyof T];
      velocity[key]=((velocity[key]-omega*c*dt)*decay) as T[keyof T];
    }
    constrain();paint({...value});
  };
  const stop=()=>{
    const now=performance.now();if(!active&&now-time>100)zero();
    advance(now);cancelAnimationFrame(frame);frame=0;active=false;complete=undefined;dragTime=undefined;time=now;
    return {value:{...value},velocity:{...velocity}};
  };
  const tick=(now:number)=>{
    frame=0;if(!active||signal.aborted)return;advance(now);
    if(resting()){value={...goal};zero();active=false;paint({...value});const done=complete;complete=undefined;done?.();}
    else frame=requestAnimationFrame(tick);
  };
  const snap=(next:T)=>{stop();value={...next};goal={...next};zero();constrain();time=performance.now();paint({...value});};
  const drag=(next:T)=>{
    const now=performance.now(),previous={...value},elapsed=dragTime===undefined?0:(now-dragTime)/1000;
    cancelAnimationFrame(frame);frame=0;active=false;complete=undefined;
    value={...next};goal={...next};
    for(const key of keys)velocity[key]=(elapsed>0&&elapsed<=.1?(value[key]-previous[key])/elapsed:0) as T[keyof T];
    constrain();dragTime=now;time=now;paint({...value});
  };
  const to=(next:T,settings:{velocity?:Partial<T>;gesture?:boolean;immediate?:boolean;done?:()=>void}={})=>{
    if(signal.aborted)return;
    if(!active&&performance.now()-time>100)zero();
    advance(performance.now());cancelAnimationFrame(frame);frame=0;
    if(settings.gesture&&(dragTime===undefined||performance.now()-dragTime>100))zero();
    for(const key of keys)if(settings.velocity?.[key]!==undefined)velocity[key]=settings.velocity[key]!;
    goal={...next};constrain();complete=settings.done;dragTime=undefined;time=performance.now();
    if(settings.immediate||resting()){active=false;value={...goal};zero();constrain();paint({...value});const done=complete;complete=undefined;done?.();}
    else {active=true;frame=requestAnimationFrame(tick);}
  };
  signal.addEventListener('abort',()=>{cancelAnimationFrame(frame);frame=0;active=false;complete=undefined;},{once:true});
  return {snap,drag,to,stop,finish:()=>to({...goal},{immediate:true,done:complete}),read:()=>{advance(performance.now());return {value:{...value},velocity:{...velocity}};},get active(){return active;}};
}
