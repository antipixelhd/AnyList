export function mountGameBannerSettings(root:HTMLElement,id:number,viewer:string,signal:AbortSignal,
  apply:(data:any)=>void,mobile=false,initial?:{backdrop?:string;pinned_banner?:string|null;background_status?:{state?:string}}) {
  const key=`game-banner:${viewer}:${id}`;
  let settings={animated:false,cycle:false},revision=0,cyclePosition:number|undefined;
  let cycleChoice:string|undefined;
  let request:Promise<void>|undefined,loaded=false;
  let timer:ReturnType<typeof setTimeout>|undefined;
  signal.addEventListener('abort',()=>clearTimeout(timer),{once:true});
  try{settings={...settings,...JSON.parse(localStorage.getItem(key)||'{}')};}catch{}
  const cycleOnEntry=settings.cycle;
  const controls=[...root.querySelectorAll<HTMLInputElement>('[data-banner-setting]')];
  controls.forEach(input=>{
    input.checked=!!settings[input.dataset.bannerSetting as keyof typeof settings];
    if(mobile&&input.dataset.bannerSetting==='animated')input.closest('label')?.setAttribute('hidden','');
  });
  const read=(refresh=false)=>{
    if(request&&!refresh)return request;
    clearTimeout(timer);
    const current=++revision;
    const pending=(async()=>{
    try{
      const response=await fetch(`/api/proxy/tracking/games/works/${id}/background?animated=${!mobile&&settings.animated}${mobile?'&provider=igdb':''}`,{signal});
      if(!response.ok)throw new Error();
      const data=await response.json();
      if(signal.aborted||current!==revision)return;
      loaded=true;
      const list:string[]=settings.cycle&&data.cycle_banners?.length?data.cycle_banners.slice(0,6):[];
      if(list.length&&!data.pinned_banner){
        if(cyclePosition===undefined){
          const positionKey=`${key}:position:${mobile?'mobile':settings.animated}`;
          cyclePosition=0;
          try{
            const saved=Number(localStorage.getItem(positionKey)||0);
            if(Number.isSafeInteger(saved)&&saved>=0)cyclePosition=saved%list.length;
            if(cycleOnEntry)localStorage.setItem(positionKey,String((cyclePosition+1)%list.length));
          }catch{}
        }
        if(!cycleChoice||!list.includes(cycleChoice))cycleChoice=list[cyclePosition%list.length];
        data.backdrop=cycleChoice;
      }
      apply(data);
      if(['pending','deferred'].includes(data.background_status?.state))timer=setTimeout(()=>void read(),Math.max(2,data.background_status.retry_after||10)*1000);
      const status=root.querySelector<HTMLElement>('[data-banner-status]');
      if(status){status.textContent=!mobile&&settings.animated&&!data.banners?.length?'No animated banners available.':settings.cycle&&list.length<2?mobile?'Only one banner available.':'Only one banner meets the rating threshold.':'';status.hidden=!status.textContent;}
    }catch{if(!signal.aborted&&current===revision){delete root.dataset.gameBannerPending;const status=root.querySelector<HTMLElement>('[data-banner-status]');if(status){status.textContent='Couldn’t load banners. Try again.';status.hidden=false;}}}
    })();
    request=pending;
    void pending.finally(()=>{if(request===pending)request=undefined;});
    return pending;
  };
  controls.forEach(input=>input.addEventListener('change',()=>{
    settings={...settings,[input.dataset.bannerSetting!]:input.checked};
    try{localStorage.setItem(key,JSON.stringify(settings));}catch{}
    void read(true);
  },{signal}));
  root.addEventListener('anylist:banner-pinned',()=>void read(true),{signal});
  // A known primary is already rendered. Optional cycling/animation may resolve
  // in the background; ordinary visits need no duplicate metadata request.
  if(!initial?.backdrop||!['ready','unavailable'].includes(initial.background_status?.state||'')
    ||!initial.pinned_banner&&(settings.cycle||!mobile&&settings.animated))void read();
  return {load:()=>loaded?Promise.resolve():read()};
}
