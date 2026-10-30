import {initializeEntryAnnotations} from './entry-annotations';
import {applyResponsiveArtwork} from './responsive-artwork';
import {mountListCoverPreview} from './list-cover-preview';
import {compactListProgress} from './tracker';
import {releaseAnnotation} from './release-annotation';
import {placeDropdown} from './dropdown-position';
import {reveal,dismiss,showMenu,hideMenu} from './ui-motion';
import {editorStore, seedEditorPage, type EditorTitle} from './editor-store';
import {showActionFailure} from './action-failure';
import {panelActive,panelUrl} from './profile-panel';
const mounted=new WeakMap<HTMLElement,AbortController>();
export function initializeProfileList(panel:HTMLElement,lifetime:AbortSignal) {
  initializeEntryAnnotations();
  mounted.get(panel)?.abort();
  const pageListeners=new AbortController();mounted.set(panel,pageListeners);
  lifetime.addEventListener('abort',()=>pageListeners.abort(),{once:true});
  const compactImageOptions={size:'w342',sizes:'(max-width:650px) 32px, 42px',route:'auto' as const};
  const updateEntryImages=() => panel.querySelectorAll<HTMLElement>('.entry-thumbnail').forEach(thumbnail => {
    const image=thumbnail.querySelector<HTMLImageElement>('.entry-cover');
    const source=thumbnail.dataset.artworkPath;
    if(image && source) applyResponsiveArtwork(image,source,compactImageOptions);
  });
  function syncAvailability(row: HTMLElement, updated: any) {
    const indicator = row.querySelector<HTMLElement>('.entry-availability');
    if (!indicator) return;
    const label = updated.availability_reason === 'airing' ? 'Currently airing' : 'New season available';
    indicator.hidden = updated.availability_dot !== true;
    indicator.title = label;
    indicator.setAttribute('aria-label', label);
  }
  const {hide:hidePreview}=mountListCoverPreview(pageListeners.signal,panel);
  const groups = panel.querySelector('#entry-groups');
  if (panel.querySelector('#list-editor-entries')) seedEditorPage(panel);
  panel.querySelectorAll<HTMLButtonElement>('[data-progress-increment]').forEach(button=>button.addEventListener('click',()=>{
    const title = editorStore.find(Number(button.dataset.progressIncrement));
    if (!title || button.disabled) return;
    const next = Number(title.entry?.progress ?? 0) + 1;
    void editorStore.write(title,{progress:next}).catch(error => {
      if (pageListeners.signal.aborted) return;
      showActionFailure('progress', title.title, error);
    });
  }, {signal:pageListeners.signal}));
  if (groups) {
    let status = panel.querySelector<HTMLElement>('.list-shell')?.dataset.initialStatus || 'all';
    const input = (id:string) => panel.querySelector(`#${id}`) as HTMLInputElement;
    const resetButton=panel.querySelector<HTMLButtonElement>('#reset-filters')!;
    const defaultSort=resetButton.dataset.defaultSort||'title';
    const mobileToggle=panel.querySelector<HTMLButtonElement>('.mobile-filter-toggle')!;
    const yearSlider=input('year-filter');
    const yearOutput=panel.querySelector<HTMLOutputElement>('#year-value')!;
    const availableYears=JSON.parse(yearSlider.dataset.years||'[]') as string[];
    const filterDropdowns=[...panel.querySelectorAll<HTMLElement>('[data-filter-dropdown]')].map(root=>({
      root,
      select:root.parentElement!.querySelector<HTMLSelectElement>('select')!,
      trigger:root.querySelector<HTMLButtonElement>('.filter-dropdown-trigger')!,
      value:root.querySelector<HTMLElement>('.filter-dropdown-trigger span')!,
      menu:root.querySelector<HTMLElement>('.filter-dropdown-options')!,
      options:[...root.querySelectorAll<HTMLButtonElement>('[data-filter-value]')],
    }));
    type FilterDropdown=(typeof filterDropdowns)[number];
    pageListeners.signal.addEventListener('abort',()=>filterDropdowns.forEach(dropdown=>{
      dropdown.menu.hidden=true;
      dropdown.trigger.setAttribute('aria-expanded','false');
      dropdown.root.append(dropdown.menu);
    }),{once:true});
    function syncDropdown(dropdown:FilterDropdown) {
      const selected=dropdown.options.find(option=>option.dataset.filterValue===dropdown.select.value) || dropdown.options[0];
      dropdown.value.textContent=selected?.textContent||'';
      dropdown.options.forEach(option=>option.setAttribute('aria-selected',String(option===selected)));
    }
    function closeDropdown(dropdown:FilterDropdown,restoreFocus=false,immediate=false) {
      if(dropdown.trigger.getAttribute('aria-expanded')!=='true')return;
      dropdown.trigger.setAttribute('aria-expanded','false');
      void hideMenu(dropdown.menu,immediate);
      if(restoreFocus)dropdown.trigger.focus();
    }
    panel.addEventListener('anylist:panel-inactive',()=>{hidePreview();filterDropdowns.forEach(dropdown=>closeDropdown(dropdown,false,true));},{signal:pageListeners.signal});
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
      },{signal:pageListeners.signal});
      dropdown.trigger.addEventListener('keydown',event=>{
        if(event.key==='ArrowDown'||event.key==='ArrowUp'){
          event.preventDefault();
          openDropdown(dropdown,true);
        }else if(event.key==='Escape'&&dropdown.trigger.getAttribute('aria-expanded')==='true'){
          event.preventDefault();event.stopPropagation();closeDropdown(dropdown);
        }
      },{signal:pageListeners.signal});
      dropdown.menu.addEventListener('click',event=>{
        const option=(event.target as Element).closest<HTMLButtonElement>('[data-filter-value]');
        if(!option)return;
        dropdown.select.value=option.dataset.filterValue??'';
        syncDropdown(dropdown);
        dropdown.select.dispatchEvent(new Event('input',{bubbles:true}));
        closeDropdown(dropdown,true);
      },{signal:pageListeners.signal});
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
      },{signal:pageListeners.signal});
    });
    document.addEventListener('click',event=>{
      filterDropdowns.forEach(dropdown=>{if(!dropdown.root.contains(event.target as Node)&&!dropdown.menu.contains(event.target as Node))closeDropdown(dropdown);});
    },{signal:pageListeners.signal});
    document.addEventListener('focusin',event=>{
      filterDropdowns.forEach(dropdown=>{if(!dropdown.root.contains(event.target as Node)&&!dropdown.menu.contains(event.target as Node))closeDropdown(dropdown);});
    },{signal:pageListeners.signal});
    document.addEventListener('scroll',event=>{
      filterDropdowns.forEach(dropdown=>{if(!dropdown.menu.contains(event.target as Node))closeDropdown(dropdown);});
    },{capture:true,signal:pageListeners.signal});
    const placeOpenDropdowns=()=>filterDropdowns.forEach(dropdown=>{
      if(dropdown.trigger.getAttribute('aria-expanded')==='true')placeDropdown(dropdown.trigger,dropdown.menu);
    });
    window.addEventListener('resize',placeOpenDropdowns,{signal:pageListeners.signal});
    window.visualViewport?.addEventListener('resize',placeOpenDropdowns,{signal:pageListeners.signal});
    window.visualViewport?.addEventListener('scroll',placeOpenDropdowns,{signal:pageListeners.signal});
    function selectedYear(){const position=Number(yearSlider.value);return position>0?availableYears[position-1]:'';}
    function updateFilterControls() {
      const position=Number(yearSlider.value);
      const yearLabel=position>0?availableYears[position-1]:'Any year';
      yearOutput.value=yearLabel;
      yearOutput.textContent=yearLabel;
      yearSlider.setAttribute('aria-valuetext',yearLabel);
      const active=[status!=='all',input('list-search').value.trim()!=='',input('genre-filter').value!=='',selectedYear()!=='',input('favorite-filter').value!=='',input('sort-filter').value!==defaultSort].filter(Boolean).length;
      mobileToggle.toggleAttribute('data-active',active>0);
      mobileToggle.setAttribute('aria-label',active>0?`Filters and sorting (${active} active)`:'Filters and sorting');
      resetButton.disabled=active===0;
      filterDropdowns.forEach(syncDropdown);
      panel.querySelectorAll<HTMLButtonElement>('[data-status]').forEach(button=>{ const active=button.dataset.status===status; button.setAttribute('aria-pressed',String(active)); button.classList.toggle('selected',active); });
    }
    const filter = () => {
      hidePreview();
      let count = 0;
      const query = input('list-search').value.toLowerCase().trim();
      panel.querySelectorAll<HTMLElement>('[data-group]').forEach(group=> {
        const table = group.querySelector('.entry-table')!;
        const rows = Array.from(group.querySelectorAll<HTMLElement>('.entry-row'));
        const sort = input('sort-filter').value;
        rows.sort((a,b)=>sort==='title' ? a.dataset.title!.localeCompare(b.dataset.title!) : sort==='updated' ? b.dataset.updated!.localeCompare(a.dataset.updated!) : Number(b.dataset[sort]) - Number(a.dataset[sort]));
        let visible = 0;
        rows.forEach((row,index)=> {
          const position = table.children[index + 1]; // The table heading stays first.
          if (position !== row) table.insertBefore(row,position || null);
          row.hidden = row.hasAttribute('data-editor-removed') || (status !== 'all' && status!==group.dataset.group) || !row.dataset.title!.includes(query) || !!(input('genre-filter').value && !JSON.parse(row.dataset.genres!).includes(input('genre-filter').value)) || !!(selectedYear() && row.dataset.year!==selectedYear()) || !!(input('favorite-filter').value && row.dataset.favorite!=='yes');
          if (!row.hidden) visible++;
        });
        group.hidden = visible===0;
        count+=visible;
      });
      panel.querySelector('#result-count')!.textContent=`${count} entries`;
      (panel.querySelector('#list-empty') as HTMLElement).hidden=count>0;
      panel.querySelectorAll<HTMLButtonElement>('[data-status]').forEach(button=>{
        const total=button.dataset.status==='all'?groups.querySelectorAll('.entry-row:not([data-editor-removed])').length:groups.querySelectorAll(`[data-group="${button.dataset.status}"] .entry-row:not([data-editor-removed])`).length;
        button.querySelector('span')!.textContent=String(total);
      });
      updateFilterControls();
    }
    panel.querySelectorAll<HTMLButtonElement>('[data-status]').forEach(button=>button.addEventListener('click',()=>{status=button.dataset.status!;panel.querySelectorAll('[data-status]').forEach(b=>b.classList.toggle('selected',b===button));filter();}));
    ['list-search','genre-filter','year-filter','sort-filter','favorite-filter'].forEach(id=>input(id).addEventListener('input',filter));
    resetButton.addEventListener('click',()=>{['list-search','genre-filter','favorite-filter'].forEach(id=>input(id).value='');input('year-filter').value='0';input('sort-filter').value=defaultSort;panel.querySelector<HTMLButtonElement>('[data-status=all]')!.click();});
    document.addEventListener('media-tracker:rating-changed', filter,{signal:pageListeners.signal});
    if (panel.querySelector('#list-editor-entries')) {
      let revision = 0, dirty = false, refreshScheduled = false;
      const scheduleRefresh = () => {
        if (!panelActive(panel) || !dirty || refreshScheduled || editorStore.hasPendingWrites() || pageListeners.signal.aborted) return;
        const editor = document.querySelector<HTMLDialogElement>('#tracking-editor');
        if (editor?.open) { editor.addEventListener('close', scheduleRefresh, {once:true, signal:pageListeners.signal}); return; }
        const focused = document.activeElement;
        if (focused instanceof HTMLInputElement && focused.closest('.list-shell')) {
          focused.addEventListener('blur', scheduleRefresh, {once:true, signal:pageListeners.signal}); return;
        }
        refreshScheduled = true;
        const version = revision;
        void (async () => {
          try {
            const response = await fetch(panelUrl(panel), {cache:'no-store', signal:pageListeners.signal});
            if (!response.ok) throw new Error('Unable to refresh the list.');
            const fresh = new DOMParser().parseFromString(await response.text(), 'text/html');
            if (pageListeners.signal.aborted) return;
            if (version !== revision || editorStore.hasPendingWrites()) { refreshScheduled = false; scheduleRefresh(); return; }
            const shell = panel.querySelector<HTMLElement>('.list-shell');
            const next = fresh.querySelector<HTMLElement>('.list-shell');
            if (!shell || !next || !fresh.querySelector('#list-editor-entries')) throw new Error('List refresh incomplete.');
            const values = Object.fromEntries(['list-search','genre-filter','favorite-filter','sort-filter'].map(id => [id, input(id).value]));
            const year = selectedYear();
            const currentStatus = status;
            const filtersOpen = mobileToggle.getAttribute('aria-expanded') === 'true';
            const currentYears = JSON.parse(next.querySelector<HTMLInputElement>('#year-filter')?.dataset.years || '[]') as string[];
            next.dataset.initialStatus = currentStatus;
            for (const [id, value] of Object.entries(values)) {
              const control = next.querySelector<HTMLInputElement | HTMLSelectElement>(`#${id}`);
              if (control) control.value = value;
            }
            const nextYear = next.querySelector<HTMLInputElement>('#year-filter');
            if (nextYear) nextYear.value = String(Math.max(0, currentYears.indexOf(year) + 1));
            shell.replaceWith(next);
            const listData = panel.querySelector('#list-editor-entries');
            const nextData = fresh.querySelector('#list-editor-entries');
            if (listData && nextData) listData.textContent = nextData.textContent;
            dirty = false;
            initializeProfileList(panel,lifetime);
            if (filtersOpen && matchMedia('(max-width:650px)').matches) panel.querySelector<HTMLButtonElement>('.mobile-filter-toggle')?.click();
          } catch (error) {
            if (!pageListeners.signal.aborted) location.reload();
          } finally { refreshScheduled = false; }
        })();
      };
      panel.addEventListener('anylist:panel-active',scheduleRefresh,{signal:pageListeners.signal});
      document.addEventListener('anylist:editor-state', event => {
        const title = (event as CustomEvent<EditorTitle>).detail;
        if(title.type==='game'||title.subject?.kind==='game')return;
        revision++;
        const button = title.id ? groups.querySelector<HTMLElement>(`[data-edit-entry="${title.id}"]`) : null;
        const row = button?.closest<HTMLElement>('.entry-row');
        if (row) {
          const entry = title.entry;
          row.toggleAttribute('data-editor-removed', !entry);
          if (entry) {
            row.dataset.score = String(entry.score ?? -1);
            row.dataset.progress = String(entry.progress ?? 0);
            row.dataset.favorite = title.favorite ? 'yes' : 'no';
            if (entry.updated_at) row.dataset.updated = entry.updated_at;
            const progress = Number(entry.progress ?? 0);
            const progressLabel = row.querySelector<HTMLElement>('[data-progress-label]');
            if (progressLabel) progressLabel.textContent = title.type === 'movie' ? String(progress) : (entry.season_position || String(progress));
            const mobileProgress = row.querySelector<HTMLElement>('[data-mobile-progress-label]');
            if (mobileProgress) mobileProgress.textContent = compactListProgress({type:title.type,progress,released_episodes:entry.released_episodes});
            const increment = row.querySelector<HTMLButtonElement>('[data-progress-increment]');
            if (increment) {
              increment.dataset.nextProgress = String(progress + 1);
              const released = entry.released_episodes ?? (title.type === 'movie' ? 1 : null);
              increment.hidden = released == null || progress >= released ||
                title.type === 'movie' && !!title.release_date && title.release_date.slice(0, 10) > new Date().toISOString().slice(0, 10);
              increment.disabled = increment.hidden;
            }
            syncAvailability(row, entry);
            const releaseLabel = entry.status === 'completed' ? releaseAnnotation(entry.new_seasons,entry.new_episodes) : null;
            let annotations = row.querySelector<HTMLElement>('.entry-annotations');
            let release = annotations?.querySelector<HTMLElement>('.unwatched-indicator');
            if (releaseLabel) {
              if (!annotations) { annotations = document.createElement('span'); annotations.className = 'entry-annotations'; row.querySelector('.entry-title')!.append(annotations); }
              if (!release) { release = document.createElement('small'); release.className = 'unwatched-indicator'; annotations.prepend(release); }
              release.textContent = releaseLabel;
            } else { release?.remove(); if (annotations && !annotations.children.length) annotations.remove(); }
            const score = row.querySelector<HTMLElement>('[data-score-label]');
            if (score) score.textContent = entry.score ? String(Number(Number(entry.score).toFixed(1))) : '—';
            const scoreButton = row.querySelector<HTMLElement>('[data-quick-rate-id]');
            if (scoreButton) { scoreButton.dataset.quickRateScore = String(entry.score ?? ''); scoreButton.dataset.quickRateMode = entry.rating_mode || 'manual'; }
            const target = groups.querySelector<HTMLElement>(`[data-group="${entry.status}"] .entry-table`);
            if (target && row.parentElement !== target) target.append(row);
          }
          filter();
        }
        if (dirty) scheduleRefresh();
      }, {signal:pageListeners.signal});
      document.addEventListener('anylist:editor-committed', event => {
        const {title,patch} = (event as CustomEvent<{title:EditorTitle;patch:Record<string,unknown>|null}>).detail;
        if(title.type==='game'||title.subject?.kind==='game')return;
        // Existing rows already receive the authoritative progress response through editor-state.
        // Replacing the list here would discard their images, focus and hover state.
        if (patch && Object.keys(patch).length === 1 && 'progress' in patch &&
            groups.querySelector('[data-edit-entry="' + title.id + '"]')) return;
        dirty = true;
        scheduleRefresh();
      }, {signal:pageListeners.signal});
    }
    updateEntryImages();
    filter();
  }
}
