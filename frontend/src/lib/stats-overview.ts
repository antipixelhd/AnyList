import {gameHeadlines,signedNumber,type GameOverview,type GameStatsResponse} from './game-statistics-data';
import {statisticsSection,statisticsPanel,statisticsSections,type StatisticsDomain,type UnifiedStatistics} from './unified-statistics-data';
import {createStatisticsReader,type StatisticsSelection} from './unified-statistics-request';
import {statisticValue} from './statistic-value';
import {bindPredictionIntent,predictionAllowed} from './predictive-loading';
import {initializeProfileSections} from './profile-section-selector';
import {panelActive,panelUrl,setPanelUrl} from './profile-panel';
import Chart from 'chart.js/auto';
import type { ChartOptions } from 'chart.js';
import type { ChartMetric, MediaScope, MetricGroup, Overview, OverviewResponse } from './stats-overview-data';
import { barChartRows, displayNumber, distributionRows, headlineValues, knownRows, metricLabels, metricValue, watchYearRows } from './stats-overview-data';
import { overviewPollDelay } from './stats-overview-request';
import { refreshStatistics } from './statistics-refresh-request';
import { mountGenres, mountStudios } from './stats-genres';
import { genreSort } from './stats-genres-data';
import { actorArtwork } from './stats-actors-data';
import { mountActors, mountStaff } from './stats-actors';
import { mountStatisticsNavigation } from './stats-navigation';
import { createMetricTooltip } from './stats-metric-tooltip';
import { chartMinimumWidth, mountChartViewport } from './stats-chart-density';
import { cancelUiMotion, reveal } from './ui-motion';

const mounted = new WeakSet<HTMLElement>();
const chartKeys = ['scores', 'episode_counts', 'release_years', 'watch_years'] as const;

export function initializeStatistics(root:HTMLElement,lifetime:AbortSignal) {
  initializeProfileSections();
  if (mounted.has(root)) return;
  mounted.add(root);
  const node = <T extends HTMLElement = HTMLElement>(selector: string) => root.querySelector<T>(selector)!;
  let response: OverviewResponse = JSON.parse(root.dataset.initial || '{}');
  let domain:StatisticsDomain=root.dataset.domain==='game'?'game':'screen';
  let page=1;
  let media: MediaScope = root.dataset.media as MediaScope;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let stopped = false;
  let refreshing = false;
  const events = new AbortController();
  events.signal.addEventListener('abort', () => cancelUiMotion(root), {once:true});
  root.addEventListener('load', event => {
    const image = event.target;
    if (image instanceof HTMLImageElement && image.matches('.stats-genre-poster img') && !image.hasAttribute('data-artwork-managed')) {
      image.setAttribute('data-motion-fade',''); reveal(image);
    }
  }, {capture:true, signal:events.signal});
  mountStatisticsNavigation(root, events.signal);
  const genres = mountGenres(root);
  const actors = mountActors(root);
  const studios = mountStudios(root);
  const staff = mountStaff(root);
  let section=statisticsSection(domain,root.dataset.section||'overview');
  let paintedView=`${domain}:${section}`;
  const rawGame=()=> (response as UnifiedStatistics).game;
  const sectionSort=(value:string)=>genreSort(root.dataset[statisticsPanel(value)==='actors'?'actorSort':statisticsPanel(value)==='staff'?'staffSort':statisticsPanel(value)==='studios'?'studioSort':'genreSort']);
  const currentSelection=():StatisticsSelection=>({domain,media,section,sort:sectionSort(section),page,generation:rawGame()?.generation});
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const chartTextStyle=getComputedStyle(node('.overview-metrics button'));
  const chartFont = {size:parseFloat(chartTextStyle.fontSize),
    weight:700 as const, family:chartTextStyle.fontFamily};
  const motion = (initial: boolean) => reducedMotion.matches ? false as const : { duration: initial ? 0 : 900, easing: 'easeInOutCubic' as const };
  const activeMotion = () => ({ active: { animation: { duration: reducedMotion.matches ? 0 : 300, easing: 'easeOutCubic' as const } } });
  const charts = new Map<string, Chart<'bar' | 'line'>>();
  const tooltips = new Map<string, ReturnType<typeof createMetricTooltip>>();
  const viewports = new Map<string, ReturnType<typeof mountChartViewport>>();
  const labelColors = new Map<string, string>();
  const pies = new Map<string, Chart<'pie'>>();
  const metrics = new Map<string, ChartMetric>(chartKeys.map(key => [key, 'titles']));
  const colors = new Map<string, string>();
  const color = (variable: string) => {
    const cached = colors.get(variable);
    if (cached) return cached;
    const probe = document.createElement('span');
    probe.style.position = 'absolute';
    probe.style.color = `var(${variable})`;
    root.append(probe);
    const result = getComputedStyle(probe).color;
    probe.remove();
    colors.set(variable, result);
    return result;
  };
  const categoryColor = (key: string) => {
    const fixed: Record<string, string> = { movie: color('--stats-cyan'), series: '#b77bdc', watching: color('--stats-cyan'), completed: '#45a987', paused: '#ed984e', dropped: '#d94d5b', planning: '#65788e', Unknown: '#65788e', US: color('--stats-cyan'), JP: '#b77bdc', GB: '#45a987' };
    const palette = ['#3b8ebf', '#b77bdc', '#45a987', '#ed984e', '#d94d5b', '#5986cb', '#b48d42', '#679e9d'];
    return fixed[key] || palette[([...key].reduce((sum, letter) => sum * 31 + letter.charCodeAt(0), 0) >>> 0) % palette.length];
  };
  function dataRows(data: Overview, key: typeof chartKeys[number]): MetricGroup[] {
    const rows=domain==='game'?data[key]:knownRows(data[key]);
    return key === 'watch_years'&&domain!=='game' ? watchYearRows(rows) : key === 'scores' || key === 'episode_counts' ? barChartRows(rows) : rows;
  }
  function table(key: string, rows: MetricGroup[]) {
    node(`[data-chart-table="${key}"]`).replaceChildren(...rows.map(row => {
      const tr = document.createElement('tr');
      [row.label, displayNumber(row.titles), ...(domain==='game'&&key==='watch_years'?[]:[displayNumber(row.time_known===false?null:row.minutes/60,1)]), ...(key === 'scores' ? [] : [displayNumber(row.mean_score, 2)])].forEach((value, index) => {
        const cell = document.createElement(index ? 'td' : 'th');
        if (cell instanceof HTMLTableCellElement && !index) cell.scope = 'row';
        cell.textContent = value;
        tr.append(cell);
      });
      return tr;
    }));
  }
  function drawMetric(data: Overview, key: typeof chartKeys[number], initial = false) {
    const rows=dataRows(data,key);let metric=metrics.get(key)!;
    if(domain==='game'&&key==='watch_years'&&metric==='hours'){metric='titles';metrics.set(key,metric);}
    const plotted = rows;
    const canvas = node<HTMLCanvasElement>(`[data-overview-chart="${key}"]`);
    canvas.parentElement!.style.setProperty('--chart-min-width', `${chartMinimumWidth(rows.length)}px`);
    const tooltip = tooltips.get(key) || createMetricTooltip(canvas,key,events.signal,(_key,row)=>({title:row.detail||(domain==='screen'&&key==='episode_counts'?`${row.label} episodes`:row.label),values:[{metric:'titles',label:domain==='game'?'Games':'Titles',value:displayNumber(row.titles)},...(domain==='game'&&key==='watch_years'?[]:[{metric:'hours',label:'Hours',value:displayNumber(row.time_known===false?null:row.minutes/60,1)}]),...(key==='scores'?[]:[{metric:'mean_score',label:'Mean score',value:displayNumber(row.mean_score,2)}])]}));
    tooltips.set(key, tooltip); tooltip.update(plotted, metric);
    const isLine = key.includes('years');
    const cyan = color('--stats-cyan'), axisText = color('--text-data'), bright = color('--bright');
    labelColors.set(key, bright);
    const empty = rows.every(row => row.titles === 0 || (metric === 'mean_score' && row.mean_score === null));
    const message = node(`[data-chart-empty="${key}"]`);
    message.hidden = !empty;
    message.textContent=domain==='game'?(metric==='mean_score'?'No rated games.':'No played games.'):(metric==='mean_score'?'No rated watched titles.':'No watched titles.');
    const highest=plotted.reduce((maximum,row)=>Math.max(maximum,metricValue(row,metric) ?? 0),0);
    canvas.setAttribute('aria-label', `${node(`[data-chart-section="${key}"] h3`).textContent}: ${metricLabels[metric]}`);
    const options: ChartOptions<'bar' | 'line'> = {
      responsive: true, maintainAspectRatio: false,
      // Touch scrolling uses native panning; a tap produces a click after release.
      events: ['mousemove', 'mouseout', 'click'],
      animation: motion(initial), transitions: activeMotion(),
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { display: false }, tooltip: { enabled:false } },
      scales: { x: { offset: !isLine || rows.length === 1, afterFit(axis) { axis.height=50; },
        grid: { display: false, drawTicks:false }, border: { display: false },
        ticks: { color: axisText, padding:(50-chartFont.size*1.2)/2, maxRotation: 0, autoSkip: true, maxTicksLimit: Math.max(1, rows.length), font: {...chartFont,lineHeight:1.2} } },
        y: { beginAtZero: true, max: metric === 'mean_score' ? 10 : highest > 0 ? highest * 1.05 : undefined, display: false } },
      layout: { padding: { top: 14 } },
    };
    const dataset = { label: metricLabels[metric], data: plotted.map(row => row ? metricValue(row, metric) : null), borderColor: cyan,
      backgroundColor: isLine ? 'rgba(30, 42, 56, 0.6)' : cyan, fill: isLine ? 'origin' as const : false,
      borderWidth: isLine ? 3 : 0,
      ...(!isLine ? { barThickness: key === 'episode_counts' ? 40 : 28,
        borderRadius: { topLeft: 4, topRight: 4, bottomLeft: 0, bottomRight: 0 }, borderSkipped: 'bottom' as const } : {}),
      pointRadius: 3.5, pointHoverRadius: 3.5, pointBackgroundColor: cyan,
      tension: isLine ? .35 : 0, cubicInterpolationMode: 'monotone' as const, spanGaps: false };
    const existing = charts.get(key);
    if (existing) {
      existing.data.labels = plotted.map(row => row?.label || '');
      Object.assign(existing.data.datasets[0], dataset);
      existing.options = options;
      // A zero-duration update is immediate while still refreshing Chart.js's
      // shared element colors (the 'none' mode can retain their old accent).
      if (initial || reducedMotion.matches) existing.stop();
      existing.update();
    } else {
      charts.set(key, new Chart<'bar' | 'line'>(canvas, { type: isLine ? 'line' : 'bar', data: { labels: plotted.map(row => row?.label || ''), datasets: [dataset] }, options,
        plugins: [tooltip.plugin, {id:'overview-axis-background', beforeDraw(chart) {
          const axis=chart.scales.x,ctx=chart.ctx;
          ctx.save();ctx.fillStyle=color('--stats-axis-background');ctx.fillRect(0,axis.top,chart.width,axis.height);ctx.restore();
        }}, { id: 'overview-values', afterDatasetsDraw(chart) {
          const ctx = chart.ctx;
          ctx.save(); ctx.fillStyle = labelColors.get(key)!; ctx.font = `${chartFont.weight} ${chartFont.size}px ${chartFont.family}`; ctx.textAlign = 'center';
          let lastRight = -Infinity;
          chart.getDatasetMeta(0).data.forEach((element, index) => {
            const value = chart.data.datasets[0].data[index];
            if (typeof value !== 'number' || value <= 0) return;
            const label = displayNumber(value, chart.data.datasets[0].label === metricLabels.mean_score ? 2 : chart.data.datasets[0].label === metricLabels.hours ? 1 : 0);
            const { x, y } = element.tooltipPosition(false), half = ctx.measureText(label).width / 2;
            if (x === null || y === null) return;
            if (x - half > lastRight + 4 && x + half < chart.width) { ctx.fillText(label, x, Math.max(y - (isLine ? 10 : 8), isLine ? 14 : 16)); lastRight = x + half; }
          });
          ctx.restore();
        } }],
      }));
    }
    table(key, rows);
    let viewport=viewports.get(key);
    if(!viewport){viewport=mountChartViewport(canvas.closest<HTMLElement>('.overview-chart-scroll')!,events.signal);viewports.set(key,viewport);}
    viewport.update(`${data.media_type}:${rows.map(row=>row.key).join(',')}`);
  }
  function drawDistribution(data: Overview, key: 'statuses' | 'formats' | 'countries', initial = false) {
    const rows = distributionRows(data, key);
    const labels = rows.map(row => row.label);
    const values = rows.map(row => row.share ?? row.titles);
    const colors = rows.map(row => categoryColor(row.key));
    const canvas = node<HTMLCanvasElement>(`[data-overview-distribution="${key}"]`);
    node(`[data-distribution-empty="${key}"]`).hidden = values.some(value => value > 0);
    const options: ChartOptions<'pie'> = { responsive: true, maintainAspectRatio: false,
      events: ['mousemove', 'mouseout', 'click'],
      animation: motion(initial), transitions: activeMotion(),
      plugins: { legend: { display: false }, tooltip: { titleFont:chartFont, bodyFont:chartFont, callbacks: { label: item => `${labels[item.dataIndex]}: ${displayNumber(rows[item.dataIndex].percent, 1)}%` } } } };
    let pie = pies.get(key);
    if (!pie) { pie = new Chart<'pie'>(canvas, { type: 'pie', data: { labels, datasets: [{ data: values, backgroundColor: colors, borderWidth: 0 }] }, options,
      plugins:[{id:'overview-distribution-interaction', beforeEvent(_chart, args) { if (args.replay) return false; }}],
    }); pies.set(key, pie); }
    else { pie.tooltip?.setActiveElements([], {x:0,y:0}); pie.setActiveElements([]); pie.data.labels = labels; Object.assign(pie.data.datasets[0], { data: values, backgroundColor: colors, borderWidth: 0 }); pie.options = options; if (initial || reducedMotion.matches) pie.stop(); pie.update(); }
    node(`[data-distribution-rows="${key}"]`).replaceChildren(...rows.map((row, index) => {
      const wrapper = document.createElement('div'), dt = document.createElement('dt'), button = document.createElement('button'), swatch = document.createElement('i');
      button.type = 'button';
      button.dataset.pie = key; button.dataset.category = row.key;
      button.setAttribute('aria-label', `${row.label}: ${displayNumber(row.titles)} titles, ${displayNumber(row.percent, 1)} percent`);
      swatch.style.backgroundColor = colors[index]; swatch.setAttribute('aria-hidden', 'true');
      button.append(swatch, document.createTextNode(row.label)); dt.append(button); wrapper.append(dt);
      [displayNumber(row.titles), `${displayNumber(row.percent, 1)}%`].forEach(text => { const dd = document.createElement('dd'); dd.textContent = text; wrapper.append(dd); });
      return wrapper;
    }));
  }
  function setResultsWaiting(waiting:boolean){
    node('.overview-results').inert=waiting;
  }
  function matchesSelection(next:OverviewResponse){
    const game=(next as UnifiedStatistics).game;
    return domain==='game'?Boolean(game&&game.section===section&&game.page===page&&(next as UnifiedStatistics).selectionSort===currentSelection().sort):!game&&(!next.overview||next.overview.media_type===media);
  }
  function render(next:OverviewResponse,initial=false){
    if(matchesSelection(next))renderNow(next,initial);
  }
  function renderNow(next: OverviewResponse, initial = false) {
    if(!matchesSelection(next))return;
    colors.clear();
    // Resolve the theme once before chart/table writes, avoiding repeated
    // synchronous layout reads during first paint and snapshot updates.
    for (const variable of ['--stats-cyan', '--stats-axis-background', '--text-data', '--bright']) color(variable);
    response = next;
    root.dataset.initial = JSON.stringify(next);
    node('.overview-error').hidden = true;
    const ready = next.status === 'ready' && next.overview !== null;
    const wasPending = node('.overview-results').hidden;
    const retainContent=!ready&&!wasPending;
    node('.overview-results').hidden = !ready&&!retainContent;
    node('.overview-pending').hidden = ready||retainContent;
    clearTimeout(timer);
    const pollDelay=domain==='game'&&next.status!=='ready'?(rawGame()?.retry_after||3)*1000:overviewPollDelay(next);
    if (pollDelay !== null) timer = setTimeout(() => { if (!stopped&&panelActive(root)) void loader.load(media,true); }, pollDelay);
    if (!ready) {
      node('[data-overview-state]').textContent = next.status === 'error' ? 'Statistics are unavailable. A retry is scheduled.' : 'Preparing statistics…';
      return;
    }
    setResultsWaiting(false);
    const data = next.overview!;
    const animateCards=!initial&&paintedView===`${domain}:${section}`;
    if(domain==='screen')root.dataset.media=data.media_type;
    updateDomainControls(true);
    (domain==='game'?gameHeadlines(section==='overview'?rawGame()?.data as GameOverview:null):headlineValues(data)).forEach((metric, index) => {
      const value = node(`[data-headline-value="${index}"]`);
      statisticValue(value,metric.value,events.signal,!initial&&section==='overview');
      value.parentElement!.title = metric.detail;
      node(`[data-headline-label="${index}"]`).textContent = metric.label;
    });
    node('[data-chart-section="episode_counts"]').hidden=domain==='screen'&&media==='movie';
    if(domain==='game'&&section==='overview')paintGameExtra(rawGame()?.data as GameOverview);
    if(!panelActive(root)&&!initial)return;
    if (section === 'overview') {
      chartKeys.forEach(key => { if (!(key === 'episode_counts' && data.media_type === 'movie')) drawMetric(data, key, initial); });
      (['statuses', 'formats', 'countries'] as const).forEach(key => drawDistribution(data, key, initial));
    }
    // Hidden sections already have server-rendered cards. Enhance only the
    // section being viewed; switching sections uses the current snapshot.
    if (section === 'genres') genres.render(data, animateCards);
    if (section === 'actors') actors.render(data, animateCards);
    if(statisticsPanel(section)==='studios')studios.render(data,animateCards);
    if (section === 'staff') staff.render(data, animateCards);
    paintedView=`${domain}:${section}`;
    if (wasPending && !initial) reveal(node('.overview-results'));
    syncLinks();updateDomainControls(true);
    node('[data-overview-announcement]').textContent = `${domain==='game'?'Games':data.media_type === 'all' ? 'All media' : data.media_type === 'movie' ? 'Movie' : 'Series'} statistics loaded.`;
  }
  let progressRunning=false;
  function statisticsBusy(busy:boolean,quiet=false){
    root.setAttribute('aria-busy',String(busy));
    setResultsWaiting(busy||response.status!=='ready'||!matchesSelection(response));
    const progress=busy&&!quiet&&panelActive(root);
    if(progress!==progressRunning){progressRunning=progress;document.dispatchEvent(new Event(progress?'anylist:route-progress-start':'anylist:route-progress-end'));}
  }
  events.signal.addEventListener('abort',()=>statisticsBusy(false,true),{once:true});
  const loader=createStatisticsReader(root.dataset.username!,events.signal,currentSelection,{
    render:next=>{
      render(next);scheduleSibling();
    },
    changed:()=>{page=1;syncUrl(false);void loader.load(media);},
    onBusy:statisticsBusy,
    onError: (message, denied) => {
      const error = node('.overview-error'); error.textContent = message; error.hidden = false;
      error.setAttribute('data-motion-fade',''); reveal(error);
      if (denied) {
        clearTimeout(timer);
        response.overview = null;
        node('.overview-results').hidden = true;
        node('.overview-pending').hidden = true;
        root.removeAttribute('data-initial');
        root.querySelectorAll('[data-chart-table],[data-distribution-rows]').forEach(element => element.replaceChildren());
        root.querySelectorAll('[data-headline-value]').forEach(element => element.textContent = '—');
        charts.forEach(chart => chart.destroy()); charts.clear(); tooltips.clear(); pies.forEach(chart => chart.destroy()); pies.clear();
        genres.render(null);
        actors.render(null);
        studios.render(null);
        staff.render(null);
      }
    },
  });
  function syncLinks() {
    root.querySelectorAll<HTMLAnchorElement>('[data-overview-media]').forEach(link => {
      const url = new URL(panelUrl(root)); url.searchParams.set('media', link.dataset.overviewMedia!);
      url.searchParams.set('section', section); url.searchParams.set('sort', root.dataset.genreSort || 'count');
      url.searchParams.set('actor_sort', root.dataset.actorSort || 'count'); url.searchParams.set('artwork', root.dataset.actorArtwork || 'media');
      url.searchParams.set('studio_sort', root.dataset.studioSort || 'count');
      url.searchParams.set('staff_sort', root.dataset.staffSort || 'count');
      link.href = url.pathname + url.search;
    });
  }
  function selectSection(next: string, push: boolean) {

    tooltips.forEach(tooltip => tooltip.dismiss());
    pies.forEach(chart => { chart.tooltip?.setActiveElements([], {x:0,y:0}); chart.setActiveElements([]); chart.draw(); });
    section=statisticsSection(domain,next);root.dataset.section=section;root.dataset.statsPanelSection=statisticsPanel(section);page=1;
    node('[data-stats-heading]').textContent = section==='developers'?'Developers':section==='publishers'?'Publishers':section === 'staff' ? 'Staff' : section === 'studios' ? 'Studios' : section === 'actors' ? 'Actors' : section === 'genres' ? 'Genres' : 'Overview';
    root.querySelectorAll<HTMLButtonElement>('.profile-section-selector__option').forEach(button => {
      const selected = button.dataset.section === section; button.classList.toggle('is-selected', selected); button.setAttribute('aria-pressed', String(selected));
    });
    if(push)syncUrl(true);
    syncLinks();updateDomainControls();
    // A section is committed with its data in one task. Keep the outgoing
    // panel painted during cold reads; never expose an unpopulated grid.
    if(domain==='game')void loader.load(media);
    else render(response,true);
    scheduleSibling();
  }
  root.addEventListener('anylist:statistics-domain-section',event=>{const next=(event as CustomEvent<{section:string}>).detail.section;if(next!==section)selectSection(next,false);},{signal:events.signal});
  root.addEventListener('profile-section-change', event => selectSection((event as CustomEvent).detail.value, true), { signal: events.signal });
  root.addEventListener('statistics-url-change', syncLinks, { signal: events.signal });
  function navigate(next: MediaScope, push: boolean) {
    tooltips.forEach(tooltip => tooltip.dismiss());
    pies.forEach(chart => { chart.tooltip?.setActiveElements([], {x:0,y:0}); chart.setActiveElements([]); chart.draw(); });
    media=next;
    clearTimeout(timer);
    if(push)syncUrl(true);
    root.querySelectorAll<HTMLElement>('[data-overview-media]').forEach(link => { if (link.dataset.overviewMedia === media) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current'); });
    void loader.load(media);
  }
  root.addEventListener('click', event => {
    const target = (event.target as HTMLElement).closest<HTMLElement>('[data-overview-media],[data-metric],[data-overview-retry],[data-pie]');
    if (!target) return;
    if (target.dataset.overviewMedia) {
      if (event instanceof MouseEvent && (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button !== 0)) return;
      event.preventDefault(); navigate(target.dataset.overviewMedia as MediaScope, true);
    } else if (target.dataset.metric) {
      const key = target.dataset.chart as typeof chartKeys[number]; metrics.set(key, target.dataset.metric as ChartMetric);
      root.querySelectorAll(`[data-chart="${key}"]`).forEach(button => button.setAttribute('aria-pressed', String((button as HTMLElement).dataset.metric === target.dataset.metric)));
      if (response.overview) drawMetric(response.overview, key);
    } else if (target.hasAttribute('data-overview-retry')) void refreshMissing();
    else if (target.dataset.pie && response.overview) {
      const key = target.dataset.pie as 'statuses' | 'formats' | 'countries';
      const index = distributionRows(response.overview, key).findIndex(row => row.key === target.dataset.category);
      const chart = pies.get(key); chart?.setActiveElements([{ datasetIndex: 0, index }]); chart?.render();
    }
  }, { signal: events.signal });
  async function refreshMissing() {
    page=1;
    if(domain==='game'){loader.clear();await loader.load(media);return;}
    if (!response.owner) { await loader.load(media); return; }
    if (refreshing) return;
    refreshing = true;
    const button = node<HTMLButtonElement>('[data-overview-retry]');
    button.disabled = true;
    button.textContent = 'Refreshing…';
    try {
      loader.clear();await refreshStatistics(events.signal);
      if (!stopped) await loader.load(media);
    } catch (error) {
      if (!stopped) {
        node('.overview-error').hidden = false;
        node('.overview-error').textContent = error instanceof Error ? error.message : 'Could not refresh statistics. Try again.';
      }
    } finally {
      refreshing = false;
      button.disabled = false;
      button.textContent = 'Refresh statistics';
    }
  }
  let siblingTimer:ReturnType<typeof setTimeout>|undefined;
  function scheduleSibling(){
    clearTimeout(siblingTimer);
    siblingTimer=setTimeout(()=>{
      if(events.signal.aborted||!panelActive(root)||!predictionAllowed()||!node('.stats-domain-navigation')?.querySelector('a[href*="/games"]'))return;
      const other:StatisticsDomain=domain==='screen'?'game':'screen';
      void loader.prefetch({...currentSelection(),domain:other,section:statisticsSection(other,section),page:1,generation:null});
    },250);
  }
  events.signal.addEventListener('abort',()=>clearTimeout(siblingTimer),{once:true});
  function syncUrl(push:boolean){
    const url=new URL(panelUrl(root));url.pathname=`/user/${encodeURIComponent(root.dataset.username!)}/stats${domain==='game'?'/games':''}`;
    url.searchParams.set('section',section);
    if(domain==='screen'){url.searchParams.set('media',media);url.searchParams.delete('page');}
    else {url.searchParams.delete('media');url.searchParams.set('sort',currentSelection().sort);url.searchParams.delete('page');}
    setPanelUrl(root,url,push);
  }
  function updateDomainControls(commit=false){
    root.dataset.domain=domain;root.dataset.section=section;root.dataset.statsPanelSection=statisticsPanel(section);root.dataset.statsRankOffset='0';
    root.querySelectorAll<HTMLButtonElement>('.profile-section-selector [data-section]').forEach(button=>{button.hidden=!statisticsSections[domain].includes(button.dataset.section!);const selected=button.dataset.section===section;button.classList.toggle('is-selected',selected);button.setAttribute('aria-pressed',String(selected));});
    node('[data-stats-heading]').textContent=node<HTMLButtonElement>(`.profile-section-selector [data-section="${section}"]`).textContent||'Overview';
    node('[data-screen-media]').hidden=domain==='game';
    root.querySelectorAll<HTMLElement>('[data-overview-media]').forEach(link=>{if(link.dataset.overviewMedia===media)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');});
    root.querySelectorAll<HTMLAnchorElement>('.stats-domain-navigation a').forEach(link=>{const game=link.getAttribute('href')?.includes('/games'),selected=game?domain==='game':domain==='screen';if(selected)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');});
    if(!commit)return;
    root.querySelectorAll<HTMLElement>('[data-chart-section="watch_years"] thead th').forEach(th=>{if(th.textContent==='Hours')th.hidden=domain==='game';});
    const titles:{[key:string]:string}={scores:'Scores',episode_counts:domain==='game'?'Recorded time':'Episode count',release_years:'Release year',watch_years:domain==='game'?'Completion year':'Watch year'};
    for(const key of chartKeys){node(`[data-chart-section="${key}"] h3`).textContent=titles[key];root.querySelectorAll<HTMLButtonElement>(`[data-chart="${key}"]`).forEach(button=>{if(button.dataset.metric==='titles')button.textContent=domain==='game'?'Games':'Titles';button.hidden=domain==='game'&&key==='watch_years'&&button.dataset.metric==='hours';});}
    for(const id of ['formats','countries'])node(`[data-distribution="${id}"]`).hidden=domain==='game';
    node('[data-game-statistics-extra]').hidden=domain!=='game';
    root.querySelectorAll<HTMLElement>('.stats-genres-toolbar button,.stats-actors-toolbar button,.stats-genre-summary dt,.stats-actor-summary dt').forEach(label=>{if(label.textContent==='Time Watched'||label.textContent==='Time Played')label.textContent=domain==='game'?'Time Played':'Time Watched';});
    root.querySelectorAll<HTMLElement>('[data-stats-panel]').forEach(panel=>panel.hidden=panel.dataset.statsPanel!==statisticsPanel(section));
  }
  function paintGameExtra(data:GameOverview){
    node('[data-game-planned-count]').textContent=displayNumber(data.planned.games);node('[data-game-planned-hours]').textContent=data.planned.estimated_seconds==null?'—':`${data.planned.known_estimate_games<data.planned.games?'≥ ':''}${displayNumber(data.planned.estimated_seconds/3600,1)}`;
    node('[data-game-replays]').textContent=data.totals.replay_games?displayNumber(data.totals.replays):'—';const undated=data.coverage.undated_completions||0;node('[data-game-undated]').hidden=!undated;node('[data-game-undated]').textContent=`${undated} completions have no date.`;
    node('[data-game-comparisons]').replaceChildren(...data.comparisons.map(row=>{const tr=document.createElement('tr');[row.label,displayNumber(row.games),signedNumber(row.mean_difference_seconds,3600),row.mean_relative_percent==null?'—':`${signedNumber(row.mean_relative_percent)}%`].forEach((text,index)=>{const cell=document.createElement(index?'td':'th');if(!index)cell.scope='row';cell.textContent=text;tr.append(cell);});return tr;}));
  }
  function selectDomain(next:StatisticsDomain,push=true){
    if(next===domain)return;
    domain=next;section=statisticsSection(domain,section);page=1;syncUrl(push);updateDomainControls();
    node('[data-stats-heading]').textContent=node<HTMLButtonElement>(`.profile-section-selector [data-section="${section}"]`).textContent||'Overview';
    void loader.load(media);scheduleSibling();
  }
  root.addEventListener('click',event=>{
    const link=(event.target as Element).closest<HTMLAnchorElement>('.stats-domain-navigation a[href]');
    if(link){if(event instanceof MouseEvent&&(event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey))return;event.preventDefault();event.stopImmediatePropagation();selectDomain(link.getAttribute('href')?.includes('/games')?'game':'screen');return;}
    const sortButton=(event.target as Element).closest<HTMLButtonElement>('button[data-genre-sort],button[data-actor-sort],button[data-studio-sort],button[data-staff-sort]');
    if(domain==='game'&&sortButton){event.preventDefault();event.stopImmediatePropagation();for(const prefix of ['genre','actor','studio','staff'])if(sortButton.dataset[prefix+'Sort'])root.dataset[prefix+'Sort']=sortButton.dataset[prefix+'Sort'];page=1;syncUrl(true);void loader.load(media);}
  },{capture:true,signal:events.signal});
  function openUrl(url:URL){
    domain=url.pathname.endsWith('/games')?'game':'screen';
    section=statisticsSection(domain,url.searchParams.get('section')||'overview');
    page=1;
    const value=url.searchParams.get('media');if(domain==='screen')media=value==='movie'||value==='series'?value:'all';
    for(const [prefix,param] of [['genre','sort'],['actor','actor_sort'],['studio','studio_sort'],['staff','staff_sort']])root.dataset[prefix+'Sort']=genreSort(url.searchParams.get(domain==='game'?'sort':param));
    root.dataset.actorArtwork=actorArtwork(url.searchParams.get('artwork'));
    syncUrl(false);syncLinks();updateDomainControls();void loader.load(media);scheduleSibling();
  }
  root.addEventListener('anylist:statistics-open',event=>openUrl(new URL((event as CustomEvent<{url:string}>).detail.url)),{signal:events.signal});
  bindPredictionIntent(root,events.signal,link=>{if(link.dataset.overviewMedia)void loader.prefetch({...currentSelection(),domain:'screen',media:link.dataset.overviewMedia as MediaScope,page:1,generation:null});if(link.closest('.stats-domain-navigation'))void loader.prefetch({...currentSelection(),domain:domain==='screen'?'game':'screen',section:statisticsSection(domain==='screen'?'game':'screen',section),page:1,generation:null});});
  let intentTimer:ReturnType<typeof setTimeout>|undefined,intentButton:HTMLElement|undefined;
  const cancelIntent=()=>{clearTimeout(intentTimer);intentButton=undefined;};
  const sectionIntent=(event:Event)=>{
    if(event instanceof PointerEvent&&event.pointerType!=='mouse')return;
    const button=(event.target as Element).closest<HTMLElement>('.profile-section-selector [data-section]');
    if(!button||button.hidden||intentButton===button)return;
    cancelIntent();intentButton=button;
    intentTimer=setTimeout(()=>{if(!events.signal.aborted&&button.isConnected&&predictionAllowed()&&button.dataset.section!==section)void loader.prefetch({...currentSelection(),section:statisticsSection(domain,button.dataset.section!),sort:sectionSort(button.dataset.section!),page:1,generation:null});},180);
  };
  root.addEventListener('pointerover',sectionIntent,{signal:events.signal,passive:true});
  root.addEventListener('focusin',sectionIntent,{signal:events.signal});
  root.addEventListener('pointerout',event=>{if(intentButton&&!intentButton.contains((event as PointerEvent).relatedTarget as Node|null))cancelIntent();},{signal:events.signal});
  root.addEventListener('focusout',cancelIntent,{signal:events.signal});
  events.signal.addEventListener('abort',cancelIntent,{once:true});
  function highlightLegend(event: Event) {
    const target = (event.target as HTMLElement).closest<HTMLElement>('[data-pie]');
    if (!target || !response.overview) return;
    const key = target.dataset.pie as 'statuses' | 'formats' | 'countries';
    const index = distributionRows(response.overview, key).findIndex(row => row.key === target.dataset.category);
    const chart = pies.get(key);
    chart?.setActiveElements([{ datasetIndex: 0, index }]); chart?.render();
  }
  root.addEventListener('focusin', highlightLegend, { signal: events.signal });
  root.addEventListener('pointerover', highlightLegend, { signal: events.signal });
  function clearHighlight() { pies.forEach(chart => { chart.setActiveElements([]); chart.render(); }); }
  root.addEventListener('focusout', clearHighlight, { signal: events.signal });
  root.addEventListener('pointerout', clearHighlight, { signal: events.signal });
  window.addEventListener('popstate',()=>{if(panelActive(root))openUrl(new URL(location.href));},{signal:events.signal});
  reducedMotion.addEventListener('change', () => { cancelUiMotion(root); if (response.overview) render(response, true); }, { signal: events.signal });
  const observer = new MutationObserver(() => { if (panelActive(root)&&response.overview) render(response, true); });
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
  // The profile runtime applies the owner/viewer accent on body, including
  // saved choices restored during client-side navigation. Repaint canvases too.
  observer.observe(document.body, { attributes: true, attributeFilter: ['style'] });
  let dirty=false;
  root.closest('[data-profile-url]')?.addEventListener('anylist:panel-inactive',()=>clearTimeout(timer),{signal:events.signal});
  const refresh=()=>{page=1;loader.clear();if(panelActive(root)){dirty=false;void loader.load(media);}else dirty=true;};
  root.closest('[data-profile-url]')?.addEventListener('anylist:panel-active',()=>{if(dirty||response.status!=='ready')refresh();else if(response.overview)render(response,true);},{signal:events.signal});
  document.addEventListener('anylist:editor-committed', () => { if (response.owner) refresh(); }, {signal:events.signal});
  lifetime.addEventListener('abort', () => { stopped = true; clearTimeout(timer); loader.stop(); events.abort(); genres.stop(); actors.stop(); studios.stop(); staff.stop(); observer.disconnect(); charts.forEach(chart => chart.destroy()); pies.forEach(chart => chart.destroy()); }, { once: true, signal: events.signal });
  loader.seed(currentSelection(),response as UnifiedStatistics);
  document.addEventListener('anylist:cache-invalidated',refresh,{signal:events.signal});
  render(response,true);updateDomainControls();scheduleSibling();
}
