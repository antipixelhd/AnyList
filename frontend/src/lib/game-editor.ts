import type {EditorTitle} from './editor-store';
import {gameTimePatch,hoursInput} from './game-editor-data';
import {localToday} from './date-time';

/** Game progress controls mounted inside the existing shared tracking form. */
export function createGameEditor(dialog:HTMLDialogElement,form:HTMLFormElement,signal:AbortSignal,confirm:(message:string)=>Promise<boolean>) {
  const input=(name:string)=>form.elements.namedItem(name) as HTMLInputElement;
  const mode=form.elements.namedItem('game_achievement_mode') as HTMLSelectElement;
  const total=input('game_total_hours'),first=input('game_first_hours'),replays=input('game_replays');
  const count=input('game_achievement_count'),percentage=input('game_achievement_percentage');
  const slider=dialog.querySelector<HTMLInputElement>('[data-game-achievement-slider]')!;
  const number=dialog.querySelector<HTMLInputElement>('[data-game-achievement-number]')!;
  const valueButton=dialog.querySelector<HTMLButtonElement>('[data-game-achievement-value]')!;
  const add=dialog.querySelector<HTMLButtonElement>('[data-game-achievement-add]')!;
  const remove=dialog.querySelector<HTMLButtonElement>('[data-game-achievement-remove]')!;
  const firstEdit=dialog.querySelector<HTMLButtonElement>('[data-game-first-edit]')!;
  const list=dialog.querySelector<HTMLElement>('[data-game-achievement-options]')!;
  const guidance=dialog.querySelector<HTMLElement>('[data-game-achievement-guidance]')!;
  const loadMore=dialog.querySelector<HTMLButtonElement>('[data-game-achievement-more]')!;
  const loadStatus=dialog.querySelector<HTMLElement>('[data-game-achievement-status]')!;
  let current:Record<string,any>={},title:EditorTitle|null=null,child=false,totalTouched=false,firstTouched=false,confirmed=false;
  let loaded=0,loadedTotal=0,busy=false,generation=0,controller:AbortController|undefined;
  let shownMode='',initialMode='',status='planning';
  const updates=new Map<number,boolean>(),originals=new Map<number,boolean>();
  const datesTouched=new Set<string>();
  const exactCount=()=>Number(current.achievements?.exact_count||0)+[...updates].reduce((sum,[id,value])=>sum+(value?1:0)-(originals.get(id)?1:0),0);
  const summary=()=>current.achievements||{};
  const progress=()=>mode.value==='percentage'?Number(percentage.value||0):mode.value==='targeted'?exactCount():Number(count.value||0);
  function renderProgress(value=progress()) {
    const percent=mode.value==='percentage',max=percent?100:Number(summary().total||0);
    slider.max=number.max=String(max);slider.step=number.step=percent?'0.1':'1';
    slider.value=number.value=String(value);slider.disabled=max===0;
    slider.style.setProperty('--game-progress',`${max?value/max*100:0}%`);
    const label=percent?`${Number(value.toFixed(1))}%`:`${value} / ${max}`;
    valueButton.textContent=label;slider.setAttribute('aria-valuetext',percent?label:`${value} of ${max} achievements`);
  }
  function renderFirst() {
    firstEdit.textContent=first.value?`${first.value} h · Edit`:'Add';
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
        checkbox.addEventListener('change',()=>{
          if(checkbox.checked===originals.get(row.id))updates.delete(row.id);else updates.set(row.id,checkbox.checked);
          count.value=String(exactCount());renderGuidance();renderProgress();
        });
        label.append(checkbox);
        const art=checkbox.checked?row.icon_url:row.locked_icon_url||row.icon_url;
        if(art){const image=document.createElement('img');image.src=art;image.alt='';image.loading='lazy';label.append(image);}
        const text=document.createElement('span');const name=document.createElement('strong');
        name.textContent=row.hidden&&!row.unlocked?'Hidden achievement':row.name;text.append(name);
        if(row.description&&(!row.hidden||row.unlocked)){const description=document.createElement('span');description.textContent=row.description;text.append(description);}
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
  function renderMode() {
    const selected=mode.value;
    setVisible('[data-game-achievement-controls]',!!selected&&!child);
    add.hidden=!!selected;remove.hidden=!selected;
    dialog.querySelector<HTMLElement>('[data-game-achievement-mode-field]')!.hidden=mode.options.length<=2;
    // The hidden select must remain readable even while the optional panel is collapsed.
    mode.disabled=child;
    number.hidden=true;valueButton.hidden=false;
    setVisible('[data-game-targeted]',selected==='targeted');
    if(selected==='targeted'){
      count.value=String(exactCount());renderGuidance();
      if(!loaded)void loadAchievements();
    }
    renderProgress();
    shownMode=selected;
  }
  add.addEventListener('click',async()=>{
    const active=generation;
    if(initialMode==='targeted'&&!confirmed) {
      if(!await confirm('Switch to simplified tracking? Your saved individual achievements will be kept.')||signal.aborted||active!==generation)return;
      confirmed=true;
    }
    mode.value=summary().total!=null?'count':'percentage';
    if(!count.value)count.value='0';if(!percentage.value)percentage.value='0';
    renderMode();slider.focus();
  },{signal});
  remove.addEventListener('click',()=>{mode.value='';renderMode();add.focus();},{signal});
  mode.addEventListener('change',async()=>{
    const active=generation;
    const desired=mode.value;
    if(shownMode==='targeted'&&desired&&desired!=='targeted') {
      mode.value=shownMode;
      if(!await confirm('Switch to simplified tracking? Your saved individual achievements will be kept.')||signal.aborted||active!==generation)return;
      confirmed=true;mode.value=desired;
    }
    renderMode();
  },{signal});
  let progressPending=false;
  async function commitProgress(raw:string) {
    if(progressPending)return;
    const max=mode.value==='percentage'?100:Number(summary().total||0);
    const parsed=Number(raw),value=Math.min(max,Math.max(0,Number.isFinite(parsed)?parsed:progress()));
    const desired=mode.value==='percentage'?Math.round(value*10)/10:Math.round(value);
    if(mode.value==='targeted'&&desired!==exactCount()) {
      const active=generation;
      progressPending=true;renderProgress();
      const accepted=await confirm('Switch to simplified tracking to change the count? Your saved individual achievements will be kept.');
      if(active!==generation)return;
      progressPending=false;
      if(!accepted||signal.aborted)return;
      confirmed=true;mode.value='count';renderMode();
    }
    (mode.value==='percentage'?percentage:count).value=String(desired);renderProgress();
  }
  slider.addEventListener('input',()=>{
    if(mode.value!=='targeted')(mode.value==='percentage'?percentage:count).value=slider.value;
    renderProgress(Number(slider.value));
  },{signal});
  slider.addEventListener('change',()=>{void commitProgress(slider.value);},{signal});
  valueButton.addEventListener('click',()=>{valueButton.hidden=true;number.hidden=false;number.focus();number.select();},{signal});
  number.addEventListener('blur',()=>{void commitProgress(number.value);number.hidden=true;valueButton.hidden=false;},{signal});
  number.addEventListener('keydown',event=>{
    if(event.key==='Enter'){event.preventDefault();number.blur();}
    if(event.key==='Escape'){event.preventDefault();event.stopPropagation();number.value=String(progress());number.blur();valueButton.focus();}
  },{signal});
  loadMore.addEventListener('click',()=>{loadMore.textContent='Load more';void loadAchievements();},{signal});
  const syncFirst=()=>{
    const finish=input('finish_date').value;
    const observedToday=finish?finish===localToday():!datesTouched.has('finish_date');
    if(title?.type==='game'&&!child&&status==='completed'&&!firstTouched&&current.completion_seconds==null&&current.status!=='completed')first.value=observedToday?total.value:'';
    if(status==='completed'&&!totalTouched&&current.total_seconds==null&&first.value)total.value=first.value;
    renderFirst();
  };
  firstEdit.addEventListener('click',()=>{first.hidden=false;firstEdit.hidden=true;first.focus();first.select();},{signal});
  first.addEventListener('blur',()=>{if(!first.checkValidity())return;first.hidden=true;firstEdit.hidden=false;renderFirst();},{signal});
  first.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();first.blur();}},{signal});
  total.addEventListener('input',()=>{totalTouched=true;syncFirst();},{signal});
  first.addEventListener('input',()=>{firstTouched=true;if(!totalTouched&&current.total_seconds==null)total.value=first.value;renderFirst();},{signal});
  form.addEventListener('input',event=>{
    const name=(event.target as HTMLInputElement).name;
    if(title?.type==='game'&&['start_date','finish_date'].includes(name)){datesTouched.add(name);syncFirst();}
  },{signal});
  form.addEventListener('change',event=>{
    const name=(event.target as HTMLInputElement).name;
    if(title?.type==='game'&&['start_date','finish_date'].includes(name)){datesTouched.add(name);syncFirst();}
  },{signal});
  signal.addEventListener('abort',()=>controller?.abort(),{once:true});
  return {
    datesTouched,
    snapshot() {
      const values=[total,first,replays,count,percentage].map(field=>field.value),selected=mode.value;
      const pending=new Map(updates),known=new Map(originals),dates=new Set(datesTouched);
      const touchedTotal=totalTouched,touchedFirst=firstTouched,simplifiedConfirmed=confirmed;
      return ()=>{
        [total,first,replays,count,percentage].forEach((field,index)=>field.value=values[index]);
        mode.value=selected;updates.clear();pending.forEach((value,key)=>updates.set(key,value));
        known.forEach((value,key)=>originals.set(key,value));datesTouched.clear();dates.forEach(value=>datesTouched.add(value));
        totalTouched=touchedTotal;firstTouched=touchedFirst;confirmed=simplifiedConfirmed;renderMode();renderFirst();
      };
    },
    reset(){generation++;controller?.abort();title=null;list.replaceChildren();updates.clear();originals.clear();datesTouched.clear();loaded=loadedTotal=0;busy=false;progressPending=false;},
    populate(next:EditorTitle) {
      this.reset();title=next;current=next.game_state||next.entry||{};
      child=['expansion','dlc'].includes(next.game_class||'');totalTouched=false;firstTouched=false;confirmed=false;
      const game=next.type==='game';
      dialog.querySelectorAll<HTMLElement>('[data-game-only]').forEach(node=>{
        node.hidden=!game||child;node.querySelectorAll<HTMLInputElement|HTMLSelectElement>('input,select').forEach(control=>control.disabled=!game||child);
      });
      if(!game)return;
      total.value=hoursInput(current.total_seconds);first.value=hoursInput(current.completion_seconds);replays.value=current.replay_count==null?'':String(current.replay_count);
      first.hidden=true;firstEdit.hidden=false;renderFirst();
      initialMode=shownMode=summary().mode||'';
      mode.replaceChildren(new Option('Not tracked',''));
      const known=summary().total!=null;
      if(!known||initialMode==='percentage')mode.add(new Option('Percentage','percentage'));
      if(known)mode.add(new Option('Count','count'));
      if(summary().definitions_available||initialMode==='targeted')mode.add(new Option('Individual achievements','targeted'));
      mode.value=initialMode;
      count.value=summary().count==null?'':String(summary().count);
      percentage.value=summary().percentage==null?'':String(summary().percentage);
      renderMode();this.statusChanged(input('status').value);
    },
    statusChanged(next:string) {
      status=next;
      const visible=title?.type==='game'&&!child&&status==='completed';
      setVisible('[data-game-first]',visible);
      if(visible)syncFirst();
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
        const value=count.value.trim()===''?null:Number(count.value);
        if(value==null||!Number.isInteger(value)||value<0||value>summary().total)throw new Error('Enter an unlocked count within the known total.');
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
