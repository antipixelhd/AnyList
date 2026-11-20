import {hoursInput,actualSeconds} from './game-editor-data';

type Context={playtime_enabled:boolean;achievements_enabled:boolean;playtime_seconds:number|null;achievement_count:number|null;acknowledged:string[]};
export function gameSteamSync(dialog:HTMLDialogElement,form:HTMLFormElement,signal:AbortSignal,confirm:(message:string)=>Promise<boolean>,restoreAchievements:(count:number)=>void,achievementState:()=>{mode:string;count:number}) {
  const time=dialog.querySelector<HTMLButtonElement>('[data-game-steam-playtime]')!;
  const achievements=dialog.querySelector<HTMLButtonElement>('[data-game-steam-achievements]')!;
  const total=form.elements.namedItem('game_total_hours') as HTMLInputElement;
  let context:Context|undefined,epoch=0,ready:Promise<void>=Promise.resolve(),active=false;
  const delta=()=>{try{return context?.playtime_seconds==null?0:(actualSeconds(total.value)||0)-context.playtime_seconds;}catch{return 0;}};
  const signedHours=(seconds:number)=>`${seconds>=0?'+':'−'}${new Intl.NumberFormat(undefined,{maximumFractionDigits:2}).format(Math.abs(seconds)/3600)} Hours`;
  const achievementMismatch=()=>{const state=achievementState();return context?.achievement_count!=null&&(state.mode!=='count'||state.count!==context.achievement_count);};
  function paint(){
    time.hidden=!active||!context?.playtime_enabled;achievements.hidden=!active||!context?.achievements_enabled;
    const difference=delta();
    time.classList.toggle('is-out-of-sync',difference!==0);
    time.title=difference?`Out of sync, ${signedHours(difference)}`:'Steam Playtime sync enabled';time.setAttribute('aria-label',time.title);
    achievements.classList.toggle('is-out-of-sync',achievementMismatch());
    achievements.title='Steam Achievements sync enabled';achievements.setAttribute('aria-label',achievementMismatch()?'Steam Achievements sync enabled. Restore synced progress':achievements.title);
  }
  for(const event of ['input','change','click'])form.addEventListener(event,()=>queueMicrotask(paint),{signal});
  time.addEventListener('click',async()=>{
    const version=epoch,difference=delta();
    if(!difference){await confirm('Playtime is in sync with Steam. You can add or reduce playtime manually; your adjustment is preserved and new Steam playtime is added to it.');return;}
    if(await confirm(`Reset playtime to Steam? This will ${difference>0?'remove':'restore'} ${signedHours(Math.abs(difference)).slice(1)} of manual adjustment.`)){
      if(version!==epoch||context?.playtime_seconds==null)return;
      total.value=hoursInput(context.playtime_seconds);total.dispatchEvent(new Event('input',{bubbles:true}));paint();
    }
  },{signal});
  achievements.addEventListener('click',()=>{if(context?.achievement_count!=null&&achievementMismatch()){restoreAchievements(context.achievement_count);paint();}},{signal});
  async function warning(field:string,message:string){
    if(!await confirm(message))return false;
    const response=await fetch(`/api/proxy/tracking/games/steam-progress/warnings/${field}`,{method:'POST',signal});
    if(!response.ok)throw new Error('Could not save the Steam sync confirmation. Try again.');
    context?.acknowledged.push(field);return true;
  }
  return {
    reset(){epoch++;active=false;context=undefined;paint();},
    load(id:number){const version=++epoch;active=true;context=undefined;paint();ready=(async()=>{
      const response=await fetch(`/api/proxy/tracking/games/works/${id}/steam-progress`,{signal});
      if(!response.ok)throw new Error('Could not check Steam sync settings. Try reopening the editor.');
      const value=await response.json();if(version===epoch){context=value;paint();}
    })();void ready.catch(()=>{});},
    async confirmChanges(patch:Record<string,any>){
      if(!active)return true;
      const timeChanged='total_seconds' in patch,achievementChanged='achievement_mode' in patch;
      if(!timeChanged&&!achievementChanged)return true;
      const version=epoch;await ready;if(version!==epoch)return false;
      if(timeChanged&&context?.playtime_enabled&&!context.acknowledged.includes('playtime')){
        if(!await warning('playtime','Steam playtime sync is enabled. Manual additions and reductions are preserved. Future Steam playtime is added to your edited total. You can reset manual adjustments using the sync icon.'))return false;
      }
      if(achievementChanged&&context?.achievements_enabled&&achievementMismatch()){
        const message='Steam achievement sync is enabled. Your manual achievement progress will be overwritten by the Steam count on the next sync for this game, even if no new achievements were unlocked.';
        if(!context.acknowledged.includes('achievements')){if(!await warning('achievements',message))return false;}
        else if(!await confirm(message))return false;
      }
      return version===epoch;
    },
  };
}
