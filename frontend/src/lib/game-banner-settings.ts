export function mountGameBannerSettings(root:HTMLElement,id:number,viewer:string,signal:AbortSignal,
  apply:(data:any)=>void,mobile=false) {
  const key=`game-banner:${viewer}:${id}`;
  let settings={animated:false,cycle:false},revision=0,cycleChoice:string|undefined;
  let timer:ReturnType<typeof setTimeout>|undefined;
  signal.addEventListener('abort',()=>clearTimeout(timer),{once:true});
  try{settings={...settings,...JSON.parse(localStorage.getItem(key)||'{}')};}catch{}
  const controls=[...root.querySelectorAll<HTMLInputElement>('[data-banner-setting]')];
  controls.forEach(input=>{
    input.checked=!!settings[input.dataset.bannerSetting as keyof typeof settings];
    if(mobile&&input.dataset.bannerSetting==='animated')input.closest('label')?.setAttribute('hidden','');
  });
  const read=async()=>{
    clearTimeout(timer);
    const current=++revision;
    try{
      const response=await fetch(`/api/proxy/tracking/games/works/${id}/background?animated=${!mobile&&settings.animated}${mobile?'&provider=igdb':''}`,{signal});
      if(!response.ok)throw new Error();
      const data=await response.json();
      if(signal.aborted||current!==revision)return;
      const list:string[]=settings.cycle&&data.cycle_banners?.length?data.cycle_banners.slice(0,6):[];
      if(list.length&&!data.pinned_banner){
        const positionKey=`${key}:position:${settings.animated}`;
        if(!cycleChoice||!list.includes(cycleChoice)){
          let position=0;
          try{const saved=Number(localStorage.getItem(positionKey)||0);position=Number.isInteger(saved)&&saved>=0?saved%list.length:0;
            localStorage.setItem(positionKey,String((position+1)%list.length));}catch{}
          cycleChoice=list[position];
        }
        data.backdrop=cycleChoice;
      }
      apply(data);
      if(['pending','deferred'].includes(data.background_status?.state))timer=setTimeout(()=>void read(),Math.max(2,data.background_status.retry_after||10)*1000);
      const status=root.querySelector<HTMLElement>('[data-banner-status]');
      if(status){status.textContent=!mobile&&settings.animated&&!data.banners?.length?'No animated banners available.':settings.cycle&&list.length<2?mobile?'Only one banner available.':'Only one banner meets the rating threshold.':'';status.hidden=!status.textContent;}
    }catch{if(!signal.aborted&&current===revision){delete root.dataset.gameBannerPending;const status=root.querySelector<HTMLElement>('[data-banner-status]');if(status){status.textContent='Couldn’t load banners. Try again.';status.hidden=false;}}}
  };
  controls.forEach(input=>input.addEventListener('change',()=>{
    settings={...settings,[input.dataset.bannerSetting!]:input.checked};
    cycleChoice=undefined;
    try{localStorage.setItem(key,JSON.stringify(settings));}catch{}
    void read();
  },{signal}));
  root.addEventListener('anylist:banner-pinned',()=>void read(),{signal});
  void read();
}
