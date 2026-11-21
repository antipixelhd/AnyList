import type {EditorTitle} from './editor-store';
import {gameTimePatch,hoursInput,achievementCountInput,firstTimeCanFillTotal} from './game-editor-data';
import {localToday} from './date-time';
import {setupEditorSelect} from './editor-select';
import {watchAchievementCatalogue,type AchievementCatalogue} from './game-achievement-catalogue';
import {gameSteamSync} from './game-steam-sync';
import {createAnchoredFieldReveal} from './anchored-field-reveal';

/** Game progress controls mounted inside the existing shared tracking form. */
export function createGameEditor(dialog:HTMLDialogElement,form:HTMLFormElement,signal:AbortSignal,confirm:(message:string)=>Promise<boolean>) {
  const input=(name:string)=>form.elements.namedItem(name) as HTMLInputElement;
  const mode=form.elements.namedItem('game_achievement_mode') as HTMLSelectElement;
  const modeControl=setupEditorSelect(mode.closest<HTMLElement>('[data-editor-select]')!,signal);
  const total=input('game_total_hours'),first=input('game_first_hours'),replays=input('game_replays');
  const count=input('game_achievement_count'),percentage=input('game_achievement_percentage');
  const number=input('game_achievement_value');
  const maximum=dialog.querySelector<HTMLElement>('[data-game-achievement-maximum]')!;
  number.setAttribute('aria-describedby','game-achievement-maximum');
  const achievements=dialog.querySelector<HTMLElement>('.game-achievement-editor')!;
  const toggle=dialog.querySelector<HTMLButtonElement>('[data-game-achievement-toggle]')!;
  const achievementSummary=dialog.querySelector<HTMLElement>('[data-game-achievement-summary]')!;
  const remove=dialog.querySelector<HTMLButtonElement>('[data-game-achievement-remove]')!;
  const firstInput=dialog.querySelector<HTMLElement>('[data-game-first-input]')!;
  const firstReading=dialog.querySelector<HTMLInputElement>('[data-game-first-reading]')!;
  const firstEmpty=dialog.querySelector<HTMLElement>('[data-game-first-empty]')!;
  const list=dialog.querySelector<HTMLElement>('[data-game-achievement-options]')!;
  const guidance=dialog.querySelector<HTMLElement>('[data-game-achievement-guidance]')!;
  const loadMore=dialog.querySelector<HTMLButtonElement>('[data-game-achievement-more]')!;
  const loadStatus=dialog.querySelector<HTMLElement>('[data-game-achievement-status]')!;
  const reveal=createAnchoredFieldReveal(dialog,dialog.querySelector('[data-game-achievement-reveal]')!,dialog.querySelector('[data-game-achievement-controls]')!,signal);
  let current:Record<string,any>={},title:EditorTitle|null=null,child=false,totalTouched=false,firstTouched=false,confirmed=false;
  let loaded=0,loadedTotal=0,busy=false,generation=0,controller:AbortController|undefined;
  let catalogueController:AbortController|undefined;
  let pendingCatalogue:AchievementCatalogue|undefined;
  let shownMode='',initialMode='',status='planning',achievementsOpen=false;
  const achievementFormat=new Intl.NumberFormat(undefined,{maximumFractionDigits:1});
  const updates=new Map<number,boolean>(),originals=new Map<number,boolean>();
  const datesTouched=new Set<string>();
  const exactCount=()=>Number(current.achievements?.exact_count||0)+[...updates].reduce((sum,[id,value])=>sum+(value?1:0)-(originals.get(id)?1:0),0);
  const summary=()=>current.achievements||{};
  const progress=()=>mode.value==='percentage'?Number(percentage.value||0):mode.value==='targeted'?exactCount():Number(count.value||0);
  const steamSync=gameSteamSync(dialog,form,signal,confirm,value=>{
    confirmed=true;mode.value='count';count.value=String(value);updates.clear();renderMode();
  },()=>({mode:mode.value,count:progress()}));
  function renderProgress(value=progress()) {
    const percent=mode.value==='percentage',max=percent?100:summary().total;
    if(max==null)number.removeAttribute('max');else number.max=String(max);
    number.step=percent?'0.1':'1';number.inputMode=percent?'decimal':'numeric';
    number.value=String(value);
    maximum.textContent=percent?'%':max==null?'':`/ ${max}`;
    const formatted=achievementFormat.format(value);
    achievementSummary.textContent=!mode.value?'Add':percent?`${formatted}%`:max==null?formatted:`${formatted} / ${achievementFormat.format(max)}`;
  }
  const renderGuidance=()=>{
    const previous=summary().count;
    guidance.textContent=`${exactCount()} selected${initialMode!=='targeted'&&previous!=null?` · Previously tracked: ${previous}`:''}`;
  };
  const setVisible=(selector:string,visible:boolean)=>{
    const node=dialog.querySelector<HTMLElement>(selector)!;node.hidden=!visible;
    node.querySelectorAll<HTMLInputElement|HTMLSelectElement>('input,select').forEach(control=>control.disabled=!visible);
  };
  async function loadAchievements() {
    if(!title||busy||signal.aborted)return;
    busy=true;loadMore.disabled=true;loadStatus.hidden=false;loadStatus.textContent='Loading achievements…';
    const active=generation;controller?.abort();controller=new AbortController();
    try {
      const response=await fetch(`/api/proxy/tracking/games/works/${title.id}/achievements?offset=${loaded}&limit=100`,{signal:controller.signal});
      if(!response.ok)throw new Error('Unable to load achievements.');
      const page=await response.json();
      if(active!==generation||signal.aborted)return;
      loadedTotal=page.total;
      for(const row of page.results) {
        originals.set(row.id,row.unlocked);
        const label=document.createElement('label');label.className='game-achievement-option';
        const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.checked=updates.get(row.id)??row.unlocked;
        const artwork=()=>checkbox.checked?row.icon_url:row.locked_icon_url||row.icon_url;
        let image:HTMLImageElement|undefined;
        checkbox.addEventListener('change',()=>{
          if(checkbox.checked===originals.get(row.id))updates.delete(row.id);else updates.set(row.id,checkbox.checked);
          if(image)image.src=artwork();
          count.value=String(exactCount());renderGuidance();renderProgress();
        });
        label.append(checkbox);
        if(artwork()){image=document.createElement('img');image.src=artwork();image.alt='';image.loading='lazy';label.append(image);}
        const text=document.createElement('span');const name=document.createElement('strong');
        // Manual selection requires the supplied identity even for Steam's hidden achievements.
        name.textContent=row.name;text.append(name);
        if(row.description){const description=document.createElement('span');description.textContent=row.description;text.append(description);}
        label.append(text);
        if(row.global_percentage!=null){const global=document.createElement('small');global.textContent=`${Number(row.global_percentage.toFixed(1))}% of players`;label.append(global);}
        list.append(label);
      }
      loaded+=page.results.length;loadMore.hidden=loaded>=loadedTotal||!page.results.length;
      loadStatus.hidden=true;
    } catch(error) {
      if(active!==generation||signal.aborted||(error as Error).name==='AbortError')return;
      loadStatus.textContent=(error as Error).message;loadMore.hidden=false;loadMore.textContent='Retry';
    } finally {
      if(active===generation){busy=false;loadMore.disabled=false;}
    }
  }
  function renderMode(animate=false) {
    reveal.change(()=>{
      const selected=mode.value;
      const expanded=achievementsOpen&&!!selected&&!child;
      if(selected==='count'&&!count.value)count.value='0';
      if(selected==='percentage'&&!percentage.value)percentage.value='0';
      if(title?.type==='game'&&!child)achievements.hidden=summary().total===0&&!selected&&!initialMode;
      setVisible('[data-game-achievement-controls]',expanded);
      toggle.setAttribute('aria-expanded',String(expanded));
      dialog.querySelector<HTMLElement>('[data-game-achievement-mode-field]')!.hidden=[...mode.options].filter(option=>!option.hidden).length<2;
      // Keep the tracking mode available to patch() while the panel is collapsed.
      mode.disabled=child;modeControl.sync();
      number.required=expanded;
      setVisible('[data-game-targeted]',expanded&&selected==='targeted');
      if(selected==='targeted'){
        count.value=String(exactCount());renderGuidance();
        if(expanded&&!loaded)void loadAchievements();
      }
      renderProgress();
      shownMode=selected;
    },animate);
  }
  function modeOptions(selected:string){
    // Unset is form state, not a tracking mode. Only Add/Remove change it.
    const unset=new Option('','');unset.hidden=true;unset.disabled=true;
    mode.replaceChildren(unset);
    mode.add(new Option('Percentage','percentage'));
    mode.add(new Option('Count','count'));
    if(summary().definitions_available||initialMode==='targeted')mode.add(new Option('Individual','targeted'));
    mode.value=selected;
  }
  function applyCatalogue(data:AchievementCatalogue){
    if(data.total==null)return; // Missing source evidence cannot erase known capabilities.
    if(progressPending||document.activeElement===number){pendingCatalogue=data;return;}
    pendingCatalogue=undefined;
    const previous=summary(),selected=mode.value;
    if(data.total===previous.total&&data.definitions_available===previous.definitions_available)return;
    current={...current,achievements:{...previous,total:data.total,availability:data.availability,
      definitions_available:data.definitions_available}};
    modeOptions(selected);renderMode();
  }
  const flushCatalogue=()=>{if(pendingCatalogue)applyCatalogue(pendingCatalogue);};
  form.addEventListener('focusout',()=>queueMicrotask(flushCatalogue),{signal});
  toggle.addEventListener('click',async()=>{
    if(achievementsOpen){
      if(progressPending||!number.reportValidity())return;
      achievementsOpen=false;
      renderMode(true);
      return;
    }
    if(mode.value){
      achievementsOpen=true;
      renderMode(true);
      return;
    }
    const active=generation;
    if(initialMode==='targeted'&&!confirmed) {
      if(!await confirm('Switch to simplified tracking? Your saved individual achievements will be kept.')||signal.aborted||active!==generation)return;
      confirmed=true;
    }
    mode.value='count';
    achievementsOpen=true;
    renderMode(true);number.focus();number.select();
  },{signal});
  remove.addEventListener('click',()=>{
    if(progressPending)return;
    mode.value='';achievementsOpen=false;renderMode(true);toggle.focus();
  },{signal});
  mode.addEventListener('change',async()=>{
    const active=generation;
    const desired=mode.value;
    if(shownMode==='targeted'&&desired&&desired!=='targeted') {
      mode.value=shownMode;modeControl.sync();
      if(!await confirm('Switch to simplified tracking? Your saved individual achievements will be kept.')||signal.aborted||active!==generation)return;
      confirmed=true;mode.value=desired;
    }
    renderMode(true);
  },{signal});
  let progressPending=false;
  async function commitProgress(raw:string) {
    if(progressPending)return;
    if(raw.trim()==='')return;
    const max=mode.value==='percentage'?100:summary().total??Infinity;
    const parsed=Number(raw),value=Math.min(max,Math.max(0,Number.isFinite(parsed)?parsed:progress()));
    const desired=mode.value==='percentage'?Math.round(value*10)/10:Math.round(value);
    if(mode.value==='targeted'&&desired!==exactCount()) {
      const active=generation;
      progressPending=true;renderProgress();
      const accepted=await confirm('Switch to simplified tracking to change the count? Your saved individual achievements will be kept.');
      if(active!==generation)return;
      progressPending=false;
      flushCatalogue();
      if(!accepted||signal.aborted)return;
      confirmed=true;mode.value='count';renderMode();
    }
    (mode.value==='percentage'?percentage:count).value=String(desired);renderProgress();
  }
  number.addEventListener('input',()=>{
    if(mode.value!=='targeted')(mode.value==='percentage'?percentage:count).value=number.value;
  },{signal});
  number.addEventListener('change',()=>{void commitProgress(number.value);},{signal});
  number.addEventListener('blur',()=>{void commitProgress(number.value);},{signal});
  number.addEventListener('keydown',event=>{
    if(event.key==='Enter' && !event.isComposing && event.keyCode !== 229){event.preventDefault();number.blur();}
    if(event.key==='Escape'){event.preventDefault();event.stopPropagation();number.value=String(progress());number.blur();}
  },{signal});
  loadMore.addEventListener('click',()=>{loadMore.textContent='Load more';void loadAchievements();},{signal});
  const syncFirst=()=>{
    const finish=input('finish_date').value;
    const observedToday=finish?finish===localToday():!datesTouched.has('finish_date');
    if(title?.type==='game'&&!child&&status==='completed'&&!firstTouched&&current.completion_seconds==null&&current.completion_source!=='unknown'&&current.status!=='completed')first.value=observedToday?total.value:'';
    if(status==='completed'&&firstTimeCanFillTotal(current,totalTouched)&&first.value)total.value=first.value;
  };
  total.addEventListener('input',()=>{totalTouched=true;syncFirst();},{signal});
  first.addEventListener('input',()=>{firstTouched=true;if(firstTimeCanFillTotal(current,totalTouched))total.value=first.value;},{signal});
  form.addEventListener('input',event=>{
    const name=(event.target as HTMLInputElement).name;
    if(title?.type==='game'&&['start_date','finish_date'].includes(name)){datesTouched.add(name);syncFirst();}
  },{signal});
  form.addEventListener('change',event=>{
    const name=(event.target as HTMLInputElement).name;
    if(title?.type==='game'&&['start_date','finish_date'].includes(name)){datesTouched.add(name);syncFirst();}
  },{signal});
  signal.addEventListener('abort',()=>{controller?.abort();catalogueController?.abort();},{once:true});
  return {
    confirmSyncChanges:(patch:Record<string,any>)=>steamSync.confirmChanges(patch),
    datesTouched,
    snapshot() {
      const values=[total,first,replays,count,percentage].map(field=>field.value),selected=mode.value;
      const pending=new Map(updates),known=new Map(originals),dates=new Set(datesTouched);
      const touchedTotal=totalTouched,touchedFirst=firstTouched,simplifiedConfirmed=confirmed,expanded=achievementsOpen;
      return ()=>{
        [total,first,replays,count,percentage].forEach((field,index)=>field.value=values[index]);
        mode.value=selected;updates.clear();pending.forEach((value,key)=>updates.set(key,value));
        known.forEach((value,key)=>originals.set(key,value));datesTouched.clear();dates.forEach(value=>datesTouched.add(value));
        totalTouched=touchedTotal;firstTouched=touchedFirst;confirmed=simplifiedConfirmed;achievementsOpen=expanded;renderMode();
      };
    },
    reset(){reveal.reset();steamSync.reset();modeControl.close();generation++;controller?.abort();catalogueController?.abort();pendingCatalogue=undefined;title=null;list.replaceChildren();updates.clear();originals.clear();datesTouched.clear();loaded=loadedTotal=0;busy=false;progressPending=false;achievementsOpen=false;},
    populate(next:EditorTitle) {
      this.reset();title=next;current=next.game_state||next.entry||{};
      child=['expansion','dlc'].includes(next.game_class||'');totalTouched=false;firstTouched=false;confirmed=false;
      const game=next.type==='game';
      dialog.querySelectorAll<HTMLElement>('[data-game-only]').forEach(node=>{
        node.hidden=!game||child;node.querySelectorAll<HTMLInputElement|HTMLSelectElement>('input,select').forEach(control=>control.disabled=!game||child);
      });
      if(!game)return;
      if(!child&&next.id)steamSync.load(next.id);
      total.value=hoursInput(current.total_seconds);first.value=hoursInput(current.completion_seconds);replays.value=current.replay_count==null?'':String(current.replay_count);
      // Use the native number rendering for the saved value as well as the editors.
      firstReading.value=first.value;
      firstEmpty.textContent=current.completion_source==='unknown'?'Unrecorded':'When completed';
      initialMode=shownMode=summary().mode||'';
      modeOptions(initialMode);
      count.value=summary().count==null?'':String(summary().count);
      percentage.value=summary().percentage==null?'':String(summary().percentage);
      renderMode();this.statusChanged(input('status').value);
      if(!child&&next.id){
        const active=generation;catalogueController=new AbortController();
        watchAchievementCatalogue(next.id,catalogueController.signal,data=>{
          if(signal.aborted||generation!==active)return;
          applyCatalogue(data);
        });
      }
    },
    statusChanged(next:string) {
      status=next;
      const editable=title?.type==='game'&&!child&&status==='completed';
      firstInput.hidden=!editable;first.disabled=!editable;
      firstReading.hidden=editable||!firstReading.value;
      firstEmpty.hidden=editable||!!firstReading.value;
      if(editable)syncFirst();
    },
    patch() {
      if(child)return {};
      if(progressPending)throw new Error('Finish choosing your achievement tracking mode before saving.');
      const patch=gameTimePatch(current,status,{total:total.value,first:first.value,replays:replays.value});
      const selected=mode.value;
      if(!selected){if(initialMode)patch.achievement_mode=null;return patch;}
      if(selected==='targeted'){
        if(initialMode!=='targeted'||updates.size){patch.achievement_mode='targeted';patch.expected_achievement_total=summary().total;patch.unlock_updates=Object.fromEntries(updates);}
      } else if(selected==='count') {
        const value=achievementCountInput(count.value,summary().total);
        if(selected!==initialMode||value!==summary().count){Object.assign(patch,{achievement_mode:'count',achievement_count:value,expected_achievement_total:summary().total,confirm_simplified:confirmed});}
      } else {
        const value=percentage.value.trim()===''?null:Number(percentage.value);
        if(value==null||!Number.isFinite(value)||value<0||value>100)throw new Error('Enter achievement progress from 0 to 100%.');
        if(selected!==initialMode||value!==summary().percentage)Object.assign(patch,{achievement_mode:'percentage',achievement_percentage:value,confirm_simplified:confirmed});
      }
      return patch;
    },
  };
}
