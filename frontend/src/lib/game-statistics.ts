import Chart from 'chart.js/auto';
import type {ChartOptions} from 'chart.js';
import {gameStatsQuery,gameStatsSections,gameHeadlines,gameChartRows,gameCard,signedNumber,type GameOverview,type GameStatsResponse,type GameStatsGroup} from './game-statistics-data';
import {createGameStatsLoader} from './game-statistics-request';
import {displayNumber,barChartRows,type ChartMetric,type MetricGroup} from './stats-overview-data';
import {mountStatisticsNavigation} from './stats-navigation';
import {createMetricTooltip} from './stats-metric-tooltip';
import {chartMinimumWidth,mountChartViewport} from './stats-chart-density';
import {carousel} from './stats-carousel';
import {genreTime} from './stats-genres-data';
import {responsiveArtwork} from './responsive-artwork';
import {initializeScrollMotion} from './scroll-motion';
import {cancelUiMotion,reveal} from './ui-motion';

const mounted=new WeakSet<HTMLElement>();
const chartKeys=['scores','hours','release_years','completion_years'] as const;
function mount(root:HTMLElement) {
  if(mounted.has(root))return;mounted.add(root);
  const node=<T extends HTMLElement=HTMLElement>(selector:string)=>root.querySelector<T>(selector)!;
  const events=new AbortController(),signal=events.signal,reduced=matchMedia('(prefers-reduced-motion: reduce)');
  let response:GameStatsResponse=JSON.parse(root.dataset.initial||'{}'),selection=gameStatsQuery(new URLSearchParams(location.search));
  let timer:ReturnType<typeof setTimeout>|undefined,attempts=0,stopped=false,showPageStart=false;
  const charts=new Map<string,Chart<'bar'|'line'>>(),tooltips=new Map<string,ReturnType<typeof createMetricTooltip>>();
  const viewports=new Map<string,ReturnType<typeof mountChartViewport>>(),metrics=new Map<string,ChartMetric>();
  const carousels=new Map<HTMLElement,ReturnType<typeof carousel>>();
  let pie:Chart<'pie'>|undefined;
  mountStatisticsNavigation(root,signal);
  initializeScrollMotion(root,signal,'.stats-genre-card,.overview-chart,.overview-distribution','stats-scroll-reveal',{animateInitial:false,downwardOnly:true,once:true});
  root.addEventListener('load',event=>{if(event.target instanceof HTMLImageElement){event.target.setAttribute('data-motion-fade','');reveal(event.target);}},{capture:true,signal});
  const color=(variable:string)=>{const probe=document.createElement('span');probe.style.color=`var(${variable})`;root.append(probe);const value=getComputedStyle(probe).color;probe.remove();return value;};
  const fontStyle=getComputedStyle(node('.overview-metrics button')),font={size:parseFloat(fontStyle.fontSize),weight:700 as const,family:fontStyle.fontFamily};
  function schedule(delay=3) {clearTimeout(timer);if(!stopped&&attempts++<20)timer=setTimeout(()=>void loader.load({...selection,generation:response.generation}),Math.max(3,Math.min(60,delay))*1000);}
  function url() {const target=new URL(location.href);target.searchParams.set('section',selection.section);target.searchParams.set('sort',selection.sort);if(selection.page>1)target.searchParams.set('page',String(selection.page));else target.searchParams.delete('page');return target;}
  function updateSelection() {
    root.dataset.section=selection.section;
    node('[data-game-stats-heading]').textContent=gameStatsSections.find(([key])=>key===selection.section)![1];
    root.querySelectorAll<HTMLElement>('.profile-section-selector [data-section]').forEach(button=>{const active=button.dataset.section===selection.section;button.setAttribute('aria-pressed',String(active));button.classList.toggle('is-selected',active);});
    root.querySelectorAll<HTMLElement>('[data-game-stats-sort]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.gameStatsSort===selection.sort)));
    node('[data-game-stats-overview]').hidden=selection.section!=='overview';node('[data-game-stats-groups]').hidden=selection.section==='overview';
  }
  function navigate(next:typeof selection,push=true) {
    showPageStart=push&&next.page!==selection.page;
    const content=node('.stats-overview-content');content.style.minHeight=`${content.getBoundingClientRect().height}px`;
    clearTimeout(timer);attempts=0;selection=next;response={...response,generation:null};
    updateSelection();node('.overview-results').hidden=true;node('.overview-pending').hidden=false;node('[data-game-stats-state]').textContent='Preparing statistics…';
    if(push)history.pushState({},'',url());
    void loader.load(selection);
  }
  function draw(key:typeof chartKeys[number],data:GameOverview,initial=false) {
    const id=`game-${key.replaceAll('_','-')}`,all=gameChartRows(data[key]);
    const rows=key==='scores'?barChartRows(all):all,isLine=key.includes('years');
    const disabled=(metric:string)=>metric==='hours'?!rows.some(row=>row.titles>0&&row.time_known!==false):metric==='mean_score'?!rows.some(row=>row.mean_score!==null):!rows.some(row=>row.titles>0);
    let metric=metrics.get(key)||'titles';if(disabled(metric))metric='titles';metrics.set(key,metric);
    root.querySelectorAll<HTMLButtonElement>(`[data-chart="${id}"]`).forEach(button=>{button.disabled=disabled(button.dataset.metric!);button.setAttribute('aria-pressed',String(button.dataset.metric===metric));});
    const value=(row:MetricGroup)=>metric==='hours'?(row.time_known===false?null:row.minutes/60):metric==='mean_score'?row.mean_score:row.titles;
    const canvas=node<HTMLCanvasElement>(`[data-overview-chart="${id}"]`),cyan=color('--stats-cyan');
    canvas.parentElement!.style.setProperty('--chart-min-width',`${chartMinimumWidth(rows.length)}px`);
    const tooltip=tooltips.get(key)||createMetricTooltip(canvas,id,signal,(_key,row)=>({title:row.detail||row.label,values:[
      {metric:'titles',label:'Games',value:displayNumber(row.titles)},
      ...(key==='completion_years'?[]:[{metric:'hours',label:'Hours recorded',value:displayNumber(row.time_known===false?null:row.minutes/60,1)}]),
      ...(key==='scores'?[]:[{metric:'mean_score',label:'Mean score',value:displayNumber(row.mean_score,2)}]),
    ]}));tooltips.set(key,tooltip);tooltip.update(rows,metric);
    const label=metric==='titles'?'Games':metric==='hours'?'Hours':'Mean score',highest=Math.max(0,...rows.map(row=>value(row)||0));
    const empty=node(`[data-chart-empty="${id}"]`);empty.hidden=rows.some(row=>value(row)!==null&&(row.titles>0));empty.textContent=metric==='mean_score'?'No rated games.':metric==='hours'?'No recorded time.':empty.dataset.emptyLabel||'No games.';
    canvas.setAttribute('aria-label',`${node(`[data-chart-section="${id}"] h3`).textContent}: ${label}`);
    const options:ChartOptions<'bar'|'line'>={responsive:true,maintainAspectRatio:false,events:['mousemove','mouseout','click'],animation:reduced.matches?false:{duration:initial?0:600,easing:'easeInOutCubic'},interaction:{mode:'index',intersect:false},plugins:{legend:{display:false},tooltip:{enabled:false}},
      scales:{x:{offset:!isLine||rows.length===1,afterFit(axis){axis.height=50;},grid:{display:false,drawTicks:false},border:{display:false},ticks:{color:color('--text-data'),padding:(50-font.size*1.2)/2,maxRotation:0,autoSkip:true,maxTicksLimit:Math.max(1,rows.length),font:{...font,lineHeight:1.2}}},y:{beginAtZero:true,max:metric==='mean_score'?10:highest>0?highest*1.05:undefined,display:false}},layout:{padding:{top:14}}};
    const dataset={label,data:rows.map(value),borderColor:cyan,backgroundColor:isLine?'rgba(30, 42, 56, 0.6)':cyan,fill:isLine?'origin' as const:false,borderWidth:isLine?3:0,
      ...(!isLine?{barThickness:28,borderRadius:{topLeft:4,topRight:4,bottomLeft:0,bottomRight:0},borderSkipped:'bottom' as const}:{}),pointRadius:3.5,pointHoverRadius:3.5,pointBackgroundColor:cyan,tension:isLine?.35:0,cubicInterpolationMode:'monotone' as const,spanGaps:false};
    const chart=charts.get(key);
    if(chart){chart.data.labels=rows.map(row=>row.label);Object.assign(chart.data.datasets[0],dataset);chart.options=options;if(initial)chart.stop();chart.update();}
    else charts.set(key,new Chart<'bar'|'line'>(canvas,{type:isLine?'line':'bar',data:{labels:rows.map(row=>row.label),datasets:[dataset]},options,plugins:[tooltip.plugin,
      {id:'game-axis-background',beforeDraw(chart){const ctx=chart.ctx,axis=chart.scales.x;ctx.save();ctx.fillStyle=color('--stats-axis-background');ctx.fillRect(0,axis.top,chart.width,axis.height);ctx.restore();}},
      {id:'game-values',afterDatasetsDraw(chart){const ctx=chart.ctx;ctx.save();ctx.fillStyle=color('--bright');ctx.font=`${font.weight} ${font.size}px ${font.family}`;ctx.textAlign='center';let right=-Infinity;chart.getDatasetMeta(0).data.forEach((element,index)=>{const number=chart.data.datasets[0].data[index];if(typeof number!=='number'||number<=0)return;const label=displayNumber(number,metrics.get(key)==='mean_score'?2:metrics.get(key)==='hours'?1:0),{x,y}=element.tooltipPosition(false);if(x===null||y===null)return;const half=ctx.measureText(label).width/2;if(x-half>right+4&&x+half<chart.width){ctx.fillText(label,x,Math.max(y-8,16));right=x+half;}});ctx.restore();}}]}));
    node(`[data-chart-table="${id}"]`).replaceChildren(...rows.map(row=>{const tr=document.createElement('tr');[row.label,displayNumber(row.titles),...(key==='completion_years'?[]:[displayNumber(row.time_known===false?null:row.minutes/60,1)]),...(key==='scores'?[]:[displayNumber(row.mean_score,2)])].forEach((text,index)=>{const cell=document.createElement(index?'td':'th');if(index===0){cell.scope='row';cell.title=row.detail||'';}cell.textContent=text;tr.append(cell);});return tr;}));
    let viewport=viewports.get(key);if(!viewport){viewport=mountChartViewport(canvas.closest<HTMLElement>('.overview-chart-scroll')!,signal);viewports.set(key,viewport);}viewport.update(rows.map(row=>row.key).join(','));
  }
  function drawOverview(data:GameOverview,initial=false) {
    gameHeadlines(data).forEach((metric,index)=>{node(`[data-headline-value="${index}"]`).textContent=metric.value;node(`[data-headline-value="${index}"]`).parentElement!.title=metric.detail;});
    node('[data-game-planned-count]').textContent=displayNumber(data.planned.games);
    node('[data-game-planned-hours]').textContent=data.planned.estimated_seconds===null?'—':`${data.planned.known_estimate_games<data.planned.games?'≥ ':''}${displayNumber(data.planned.estimated_seconds/3600,1)}`;
    node('[data-game-replays]').textContent=data.totals.replay_games?displayNumber(data.totals.replays):'—';
    const undated=node('[data-game-undated]'),count=data.coverage.undated_completions;undated.hidden=!count;undated.textContent=`${displayNumber(count)} ${count===1?'completion has':'completions have'} no date.`;
    node('[data-game-comparisons]').replaceChildren(...data.comparisons.map(row=>{const tr=document.createElement('tr');[row.label,displayNumber(row.games),signedNumber(row.mean_difference_seconds,3600),row.mean_relative_percent===null?'—':`${signedNumber(row.mean_relative_percent)}%`].forEach((text,index)=>{const cell=document.createElement(index?'td':'th');if(index===0)cell.scope='row';cell.textContent=text;tr.append(cell);});return tr;}));
    chartKeys.forEach(key=>draw(key,data,initial));
    const colors=[color('--muted'),color('--stats-cyan'),'#ed984e','#45a987','#d94d5b'];
    const rows=data.statuses,labels=rows.map(row=>row.label),values=rows.map(row=>row.games),options:ChartOptions<'pie'>={responsive:true,maintainAspectRatio:false,animation:reduced.matches?false:{duration:initial?0:600},events:['mousemove','mouseout','click'],plugins:{legend:{display:false},tooltip:{titleFont:font,bodyFont:font,callbacks:{label:item=>`${labels[item.dataIndex]}: ${displayNumber(data.totals.listed_games?values[item.dataIndex]/data.totals.listed_games*100:0,1)}%`}}}};
    if(pie){pie.data.labels=labels;pie.data.datasets[0].data=values;pie.data.datasets[0].backgroundColor=colors;pie.options=options;pie.update();}
    else pie=new Chart<'pie'>(node<HTMLCanvasElement>('[data-overview-distribution="statuses"]'),{type:'pie',data:{labels,datasets:[{data:values,backgroundColor:colors,borderWidth:0}]},options});
    node('[data-distribution-empty="statuses"]').hidden=values.some(value=>value>0);
    node('[data-distribution-rows="statuses"]').replaceChildren(...rows.map((row,index)=>{const div=document.createElement('div'),dt=document.createElement('dt'),button=document.createElement('button'),dot=document.createElement('i'),count=document.createElement('dd'),percent=document.createElement('dd');button.type='button';button.dataset.gameStatus=String(index);button.disabled=row.games===0;dot.style.background=colors[index];dot.setAttribute('aria-hidden','true');button.append(dot,document.createTextNode(row.label));count.textContent=displayNumber(row.games);percent.textContent=`${displayNumber(data.totals.listed_games?row.games/data.totals.listed_games*100:0,1)}%`;button.setAttribute('aria-label',`${row.label}: ${count.textContent} games, ${percent.textContent}`);dt.append(button);div.append(dt,count,percent);return div;}));
  }
  function artwork(link:HTMLElement,url:string|null,label:string,portrait=false,personPoster=false) {
    link.querySelectorAll('img,.stats-genre-placeholder,.stats-actor-placeholder').forEach(child=>child.remove());
    const art=responsiveArtwork(url,{size:'w185',sizes:portrait?'(max-width:650px) 80px, 110px':personPoster?'(max-width:650px) 15vw, 70px':'(max-width:650px) 20vw, 90px',route:'direct'});
    const placeholder=()=>{const span=document.createElement('span');span.className=portrait?'stats-actor-placeholder':'stats-genre-placeholder';span.textContent=label.slice(0,1);span.setAttribute('aria-hidden','true');link.prepend(span);};
    if(!art){placeholder();return;}const img=document.createElement('img');img.alt='';img.loading='lazy';img.draggable=false;if(art.srcset)img.srcset=art.srcset;if(art.sizes)img.sizes=art.sizes;img.src=art.src;img.addEventListener('error',()=>{img.remove();placeholder();},{once:true,signal});link.prepend(img);
  }
  function drawGroups(rows:GameStatsGroup[]) {
    const people=selection.section==='actors'||selection.section==='staff',prefix=people?'staff':'studio',grid=node('[data-game-stats-grid]');
    grid.classList.toggle('stats-actors-grid',people);
    const existing=new Map([...grid.children].map(card=>[(card as HTMLElement).dataset[`${prefix}Key`],card as HTMLElement]));
    const cards=rows.map((row,index)=>{
      const signature=JSON.stringify(gameCard(row));let card=existing.get(row.key);
      if(!card||card.dataset[`${prefix}Signature`]!==signature){
        card=node<HTMLTemplateElement>(people?'[data-game-person-template]':'[data-game-group-template]').content.firstElementChild!.cloneNode(true) as HTMLElement;
        card.dataset[`${prefix}Key`]=row.key;card.dataset[`${prefix}Signature`]=signature;card.setAttribute('aria-label',row.label);
        const q=<T extends HTMLElement=HTMLElement>(selector:string)=>card!.querySelector<T>(selector)!;
        const link=q<HTMLAnchorElement>(`[data-${prefix}-link]`);link.textContent=row.label;if(row.href)link.href=row.href;else link.removeAttribute('href');link.title=row.label;
        q(`[data-${prefix}-count]`).textContent=displayNumber(row.games);q(`[data-${prefix}-score]`).textContent=displayNumber(row.mean_score,2);q(`[data-${prefix}-time]`).textContent=genreTime(gameCard(row));
        if(!people)q(`[data-${prefix}-no-ratings]`).hidden=row.top_titles.length>0;
        if(people){const portrait=q<HTMLAnchorElement>('[data-staff-portrait]');if(row.href)portrait.href=row.href;portrait.setAttribute('aria-label',row.label);artwork(portrait,row.image,row.label,true);}
        const track=q('[data-genre-track]');track.setAttribute('aria-label',`Highest-rated ${row.label} games`);track.replaceChildren(...row.top_titles.map(title=>{const link=node<HTMLTemplateElement>('[data-game-poster-template]').content.firstElementChild!.cloneNode(true) as HTMLAnchorElement;link.href=title.href;link.setAttribute('aria-label',title.title);link.querySelector('.stats-genre-tooltip')!.textContent=title.title;artwork(link,title.poster,title.title,false,people);return link;}));
        for(const direction of [-1,1])q(`[data-${prefix}-scroll="${direction}"]`).setAttribute('aria-label',`${direction<0?'Previous':'Next'} ${row.label} games`);
      }
      const rank=card.querySelector(`[data-${prefix}-rank]`)!;rank.textContent=String((selection.page-1)*24+index+1);rank.setAttribute('aria-label',`Rank ${rank.textContent}`);return card;
    });
    for(const [card,controller] of carousels)if(!cards.includes(card)){controller.stop();carousels.delete(card);}
    grid.replaceChildren(...cards);cards.forEach(card=>{if(!carousels.has(card))carousels.set(card,carousel(card.querySelector('[data-genre-track]')!,reduced));else carousels.get(card)!.bounds();});
    node('[data-game-groups-empty]').hidden=rows.length>0;node('[data-game-stats-page-label]').textContent=`Page ${selection.page}`;
    for(const direction of [-1,1]){const link=node<HTMLAnchorElement>(`[data-game-stats-page="${direction}"]`),disabled=direction<0?selection.page<=1:!response.has_more;link.setAttribute('aria-disabled',String(disabled));link.tabIndex=disabled?-1:0;const target=url();target.searchParams.set('page',String(selection.page+direction));if(disabled)link.removeAttribute('href');else link.href=target.toString();}
  }
  function render(data:GameStatsResponse,initial=false) {
    response=data;updateSelection();node('[data-game-stats-error]').hidden=true;
    const ready=data.status==='ready';node('.overview-results').hidden=!ready;node('.overview-pending').hidden=ready;
    node('[data-game-stats-state]').textContent=data.status==='error'?'Statistics are unavailable. A retry is scheduled.':'Preparing statistics…';
    if(!ready){schedule(data.retry_after);return;}clearTimeout(timer);attempts=0;
    if(selection.section!=='overview'&&selection.page>Math.max(1,Math.ceil((data.total||0)/24))){navigate({...selection,page:Math.max(1,Math.ceil((data.total||0)/24))},false);history.replaceState({},'',url());return;}
    if(selection.section==='overview')drawOverview(data.data as GameOverview,initial);else drawGroups(data.data as GameStatsGroup[]);
    node('.stats-overview-content').style.removeProperty('min-height');
    if(showPageStart){showPageStart=false;node('[data-game-stats-heading]').scrollIntoView({block:'start'});}
    if(!initial)reveal(node('.overview-results'));
    node('[data-game-stats-announcement]').textContent=`${gameStatsSections.find(([key])=>key===selection.section)![1]} statistics loaded${selection.section==='overview'?'':`, page ${selection.page}`}.`;
  }
  const loader=createGameStatsLoader(root.dataset.username!,{render,busy:busy=>root.setAttribute('aria-busy',String(busy)),changed:()=>{navigate({...selection,page:1},false);history.replaceState({},'',url());},error:(message,denied)=>{const error=node('[data-game-stats-error]');error.textContent=message;error.hidden=false;if(denied){clearTimeout(timer);node('.overview-results').hidden=true;node('.overview-pending').hidden=true;}else schedule(10);}});
  root.addEventListener('profile-section-change',event=>{const section=(event as CustomEvent).detail.value;if(gameStatsSections.some(([key])=>key===section))navigate({...selection,section,page:1});},{signal});
  root.addEventListener('click',event=>{
    const target=(event.target as Element).closest<HTMLElement>('[data-game-stats-sort],[data-game-stats-page],[data-game-stats-retry],[data-metric],[data-studio-scroll],[data-staff-scroll],[data-game-status]');if(!target)return;
    if(target.dataset.gameStatsSort)navigate({...selection,sort:target.dataset.gameStatsSort as typeof selection.sort,page:1});
    else if(target.hasAttribute('data-game-stats-page')){if(event instanceof MouseEvent&&(event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||event.button!==0))return;event.preventDefault();if(target.getAttribute('aria-disabled')!=='true')navigate({...selection,page:selection.page+Number(target.dataset.gameStatsPage)});}
    else if(target.hasAttribute('data-game-stats-retry')){attempts=0;void loader.load(selection);}
    else if(target.dataset.metric){const key=target.dataset.chart!.slice(5).replaceAll('-','_') as typeof chartKeys[number];metrics.set(key,target.dataset.metric as ChartMetric);root.querySelectorAll(`[data-chart="${target.dataset.chart}"]`).forEach(button=>button.setAttribute('aria-pressed',String((button as HTMLElement).dataset.metric===target.dataset.metric)));if(response.status==='ready')draw(key,response.data as GameOverview);}
    else if(target.dataset.gameStatus){pie?.setActiveElements([{datasetIndex:0,index:Number(target.dataset.gameStatus)}]);pie?.render();}
    else {const card=target.closest<HTMLElement>('.stats-genre-card')!;carousels.get(card)?.scroll(Number(target.dataset.studioScroll||target.dataset.staffScroll));}
  },{signal});
  window.addEventListener('popstate',()=>navigate(gameStatsQuery(new URLSearchParams(location.search)),false),{signal});
  document.addEventListener('anylist:editor-committed',event=>{const detail=(event as CustomEvent).detail;if(response.owner&&detail.title?.type==='game'){attempts=0;void loader.load(selection);}},{signal});
  const observer=new MutationObserver(()=>{if(selection.section==='overview'&&response.status==='ready')drawOverview(response.data as GameOverview,true);});observer.observe(document.documentElement,{attributes:true,attributeFilter:['class']});observer.observe(document.body,{attributes:true,attributeFilter:['style']});
  reduced.addEventListener('change',()=>{cancelUiMotion(root);if(selection.section==='overview'&&response.status==='ready')drawOverview(response.data as GameOverview,true);},{signal});
  document.addEventListener('astro:before-swap',()=>{stopped=true;clearTimeout(timer);loader.stop();events.abort();observer.disconnect();charts.forEach(chart=>chart.destroy());pie?.destroy();carousels.forEach(controller=>controller.stop());cancelUiMotion(root);},{once:true,signal});
  render(response,true);
}
function setup(){const root=document.querySelector<HTMLElement>('#game-statistics');if(root)mount(root);}
document.addEventListener('astro:page-load',setup);setup();
