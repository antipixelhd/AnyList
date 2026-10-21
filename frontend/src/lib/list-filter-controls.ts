import {placeDropdown} from './dropdown-position';
import {reveal,dismiss,showMenu,hideMenu} from './ui-motion';

/** Shared dropdowns, year slider and responsive filter panel for personal lists. */
export function mountListFilterControls(root:HTMLElement,signal:AbortSignal) {
    const input=(id:string)=>root.querySelector<HTMLInputElement>(`#${id}`)!;
    const mobileToggle=root.querySelector<HTMLButtonElement>('.mobile-filter-toggle')!;
    const resetButton=root.querySelector<HTMLButtonElement>('#reset-filters')!;
    const yearSlider=input('year-filter');
    const yearOutput=root.querySelector<HTMLOutputElement>('#year-value')!;
    const yearField=root.querySelector<HTMLInputElement>('[data-list-selected-year]')!;
    const availableYears=JSON.parse(yearSlider.dataset.years||'[]') as string[];
    const filterDropdowns=[...root.querySelectorAll<HTMLElement>('[data-filter-dropdown]')].map(root=>({
      root,
      select:root.parentElement!.querySelector<HTMLSelectElement>('select')!,
      trigger:root.querySelector<HTMLButtonElement>('.filter-dropdown-trigger')!,
      value:root.querySelector<HTMLElement>('.filter-dropdown-trigger span')!,
      menu:root.querySelector<HTMLElement>('.filter-dropdown-options')!,
      options:[...root.querySelectorAll<HTMLButtonElement>('[data-filter-value]')],
    }));
    type FilterDropdown=(typeof filterDropdowns)[number];
    signal.addEventListener('abort',()=>filterDropdowns.forEach(dropdown=>{
      dropdown.menu.hidden=true;
      dropdown.trigger.setAttribute('aria-expanded','false');
      dropdown.root.append(dropdown.menu);
    }),{once:true});
    function syncDropdown(dropdown:FilterDropdown) {
      const selected=dropdown.options.find(option=>option.dataset.filterValue===dropdown.select.value) || dropdown.options[0];
      dropdown.value.textContent=selected?.textContent||'';
      dropdown.options.forEach(option=>option.setAttribute('aria-selected',String(option===selected)));
    }
    function closeDropdown(dropdown:FilterDropdown,restoreFocus=false) {
      if(dropdown.trigger.getAttribute('aria-expanded')!=='true')return;
      dropdown.trigger.setAttribute('aria-expanded','false');
      void hideMenu(dropdown.menu);
      if(restoreFocus)dropdown.trigger.focus();
    }
    function openDropdown(dropdown:FilterDropdown,focusOption=false) {
      filterDropdowns.forEach(other=>{if(other!==dropdown)closeDropdown(other);});
      document.body.append(dropdown.menu);
      placeDropdown(dropdown.trigger,dropdown.menu);
      showMenu(dropdown.menu);
      dropdown.trigger.setAttribute('aria-expanded','true');
      if(focusOption)(dropdown.options.find(option=>option.dataset.filterValue===dropdown.select.value)||dropdown.options[0])?.focus();
    }
    filterDropdowns.forEach(dropdown=>{
      syncDropdown(dropdown);
      dropdown.trigger.addEventListener('click',()=>{
        if(dropdown.trigger.getAttribute('aria-expanded')==='true')closeDropdown(dropdown);
        else openDropdown(dropdown);
      },{signal});
      dropdown.trigger.addEventListener('keydown',event=>{
        if(event.key==='ArrowDown'||event.key==='ArrowUp'){
          event.preventDefault();
          openDropdown(dropdown,true);
        }else if(event.key==='Escape'&&dropdown.trigger.getAttribute('aria-expanded')==='true'){
          event.preventDefault();event.stopPropagation();closeDropdown(dropdown);
        }
      },{signal});
      dropdown.menu.addEventListener('click',event=>{
        const option=(event.target as Element).closest<HTMLButtonElement>('[data-filter-value]');
        if(!option)return;
        dropdown.select.value=option.dataset.filterValue??'';
        syncDropdown(dropdown);
        dropdown.select.dispatchEvent(new Event('input',{bubbles:true}));
        closeDropdown(dropdown,true);
      },{signal});
      dropdown.menu.addEventListener('keydown',event=>{
        if(event.key==='Escape'){
          event.preventDefault();event.stopPropagation();closeDropdown(dropdown,true);return;
        }
        const index=dropdown.options.indexOf(document.activeElement as HTMLButtonElement);
        const next=event.key==='Home'?0:event.key==='End'?dropdown.options.length-1:event.key==='ArrowDown'?(index+1)%dropdown.options.length:event.key==='ArrowUp'?(index-1+dropdown.options.length)%dropdown.options.length:-1;
        if(next>=0){event.preventDefault();dropdown.options[next]?.focus();return;}
        if(event.key.length===1&&!event.ctrlKey&&!event.altKey&&!event.metaKey){
          const key=event.key.toLocaleLowerCase();
          const ordered=[...dropdown.options.slice(index+1),...dropdown.options.slice(0,index+1)];
          const match=ordered.find(option=>option.textContent?.trim().toLocaleLowerCase().startsWith(key));
          if(match){event.preventDefault();match.focus();}
        }
      },{signal});
    });
    document.addEventListener('click',event=>{
      filterDropdowns.forEach(dropdown=>{if(!dropdown.root.contains(event.target as Node)&&!dropdown.menu.contains(event.target as Node))closeDropdown(dropdown);});
    },{signal});
    document.addEventListener('focusin',event=>{
      filterDropdowns.forEach(dropdown=>{if(!dropdown.root.contains(event.target as Node)&&!dropdown.menu.contains(event.target as Node))closeDropdown(dropdown);});
    },{signal});
    document.addEventListener('scroll',event=>{
      filterDropdowns.forEach(dropdown=>{if(!dropdown.menu.contains(event.target as Node))closeDropdown(dropdown);});
    },{capture:true,signal});
    const placeOpenDropdowns=()=>filterDropdowns.forEach(dropdown=>{
      if(dropdown.trigger.getAttribute('aria-expanded')==='true')placeDropdown(dropdown.trigger,dropdown.menu);
    });
    window.addEventListener('resize',placeOpenDropdowns,{signal});
    window.visualViewport?.addEventListener('resize',placeOpenDropdowns,{signal});
    window.visualViewport?.addEventListener('scroll',placeOpenDropdowns,{signal});
    const filters=root;
    const filterPanel=root.querySelector<HTMLElement>('#list-filter-panel')!;
    const mobileFilters=matchMedia('(max-width:650px)');
    filterPanel.inert=mobileFilters.matches;
    function setFiltersOpen(open:boolean){
      if(!mobileFilters.matches)return;
      mobileToggle.setAttribute('aria-expanded',String(open));
      filterPanel.inert=!open;
      if(open){filters.classList.add('open');reveal(filterPanel);}
      else{filterDropdowns.forEach(dropdown=>closeDropdown(dropdown));void dismiss(filterPanel).then(current=>{if(current&&mobileFilters.matches)filters.classList.remove('open');});mobileToggle.focus();}
    }
    mobileToggle.addEventListener('click',()=>setFiltersOpen(mobileToggle.getAttribute('aria-expanded')!=='true'),{signal});
    mobileFilters.addEventListener('change',()=>{
      const focusInside=filterPanel.contains(document.activeElement);
      filterDropdowns.forEach(dropdown=>closeDropdown(dropdown));
      reveal(filterPanel);
      filters.classList.remove('open');
      mobileToggle.setAttribute('aria-expanded','false');
      filterPanel.inert=mobileFilters.matches;
      if(mobileFilters.matches&&focusInside)mobileToggle.focus();
      else if(!mobileFilters.matches&&document.activeElement===mobileToggle)input('list-search').focus();
    },{signal});
    document.addEventListener('keydown',event=>{if(!event.defaultPrevented&&event.key==='Escape'&&filters.classList.contains('open'))setFiltersOpen(false);},{signal});
    function selectedYear(){const position=Number(yearSlider.value);return position>0?availableYears[position-1]:'';}
    function syncYear(){
      const year=selectedYear();yearField.value=year;
      yearOutput.value=year||'Any year';yearOutput.textContent=year||'Any year';
      yearSlider.setAttribute('aria-valuetext',year||'Any year');
    }
    yearSlider.addEventListener('input',syncYear,{signal});
    syncYear();
    return {selectedYear,sync(status:string,defaultSort:string){
      syncYear();
      const active=[status!=='all',input('list-search').value.trim()!=='',input('genre-filter').value!=='',selectedYear()!=='',input('favorite-filter').value!=='',input('sort-filter').value!==defaultSort].filter(Boolean).length;
      mobileToggle.toggleAttribute('data-active',active>0);
      mobileToggle.setAttribute('aria-label',active>0?`Filters and sorting (${active} active)`:'Filters and sorting');
      resetButton.disabled=active===0;
      filterDropdowns.forEach(syncDropdown);
      root.querySelectorAll<HTMLButtonElement>('[data-status]').forEach(button=>{
        button.setAttribute('aria-pressed',String(button.dataset.status===status));
        button.classList.toggle('selected',button.dataset.status===status);
      });
    }};
}
