import Chart from 'chart.js/auto';
import type { ChartOptions } from 'chart.js';
import type { ChartMetric, MediaScope, MetricGroup, Overview, OverviewResponse } from './stats-overview-data';
import { barChartRows, displayNumber, distributionRows, headlineValues, knownRows, metricLabels, metricValue, watchYearRows } from './stats-overview-data';
import { createOverviewLoader, overviewPollDelay } from './stats-overview-request';
import { refreshStatistics } from './statistics-refresh-request';
import { mountGenres, mountStudios } from './stats-genres';
import { genreSort } from './stats-genres-data';
import { actorArtwork } from './stats-actors-data';
import { mountActors, mountStaff } from './stats-actors';
import { mountStatisticsNavigation } from './stats-navigation';
import { createMetricTooltip } from './stats-metric-tooltip';
import { chartMinimumWidth, mountChartViewport } from './stats-chart-density';
import { createContentTransition } from './content-motion';
import { initializeScrollMotion } from './scroll-motion';
import { cancelUiMotion, reveal } from './ui-motion';

const mounted = new WeakSet<HTMLElement>();
const chartKeys = ['scores', 'episode_counts', 'release_years', 'watch_years'] as const;

function mount(root: HTMLElement) {
  if (mounted.has(root)) return;
  mounted.add(root);
  const node = <T extends HTMLElement = HTMLElement>(selector: string) => root.querySelector<T>(selector)!;
  let response: OverviewResponse = JSON.parse(root.dataset.initial || '{}');
  let media: MediaScope = root.dataset.media as MediaScope;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let stopped = false;
  let refreshing = false;
  const events = new AbortController();
  const switchPanel = createContentTransition(node('.overview-results'), events.signal);
  initializeScrollMotion(root, events.signal, '.stats-genre-card,.overview-chart,.overview-distribution', 'stats-scroll-reveal', {animateInitial:false, downwardOnly:true, once:true});
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
  let section = root.dataset.section === 'genres' || root.dataset.section === 'actors' || root.dataset.section === 'studios' || root.dataset.section === 'staff' ? root.dataset.section : 'overview';
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
    const rows = knownRows(data[key]);
    return key === 'watch_years' ? watchYearRows(rows) : key === 'scores' || key === 'episode_counts' ? barChartRows(rows) : rows;
  }
  function table(key: string, rows: MetricGroup[]) {
    node(`[data-chart-table="${key}"]`).replaceChildren(...rows.map(row => {
      const tr = document.createElement('tr');
      [row.label, displayNumber(row.titles), displayNumber(row.minutes / 60, 1), ...(key === 'scores' ? [] : [displayNumber(row.mean_score, 2)])].forEach((value, index) => {
        const cell = document.createElement(index ? 'td' : 'th');
        if (cell instanceof HTMLTableCellElement && !index) cell.scope = 'row';
        cell.textContent = value;
        tr.append(cell);
      });
      return tr;
    }));
  }
  function drawMetric(data: Overview, key: typeof chartKeys[number], initial = false) {
    const rows = dataRows(data, key), metric = metrics.get(key)!;
    const plotted = rows;
    const canvas = node<HTMLCanvasElement>(`[data-overview-chart="${key}"]`);
    canvas.parentElement!.style.setProperty('--chart-min-width', `${chartMinimumWidth(rows.length)}px`);
    const tooltip = tooltips.get(key) || createMetricTooltip(canvas, key, events.signal);
    tooltips.set(key, tooltip); tooltip.update(plotted, metric);
    const isLine = key.includes('years');
    const cyan = color('--stats-cyan'), axisText = color('--text-data'), bright = color('--bright');
    labelColors.set(key, bright);
    const empty = rows.every(row => row.titles === 0 || (metric === 'mean_score' && row.mean_score === null));
    const message = node(`[data-chart-empty="${key}"]`);
    message.hidden = !empty;
    message.textContent = metric === 'mean_score' ? 'No rated watched titles.' : 'No watched titles.';
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
  function render(next: OverviewResponse, initial = false) {
    colors.clear();
    // Resolve the theme once before chart/table writes, avoiding repeated
    // synchronous layout reads during first paint and snapshot updates.
    for (const variable of ['--stats-cyan', '--stats-axis-background', '--text-data', '--bright']) color(variable);
    response = next;
    root.dataset.initial = JSON.stringify(next);
    node('.overview-error').hidden = true;
    const ready = next.status === 'ready' && next.overview !== null;
    const wasPending = node('.overview-results').hidden;
    node('.overview-results').hidden = !ready;
    node('.overview-pending').hidden = ready;
    clearTimeout(timer);
    const pollDelay = overviewPollDelay(next);
    if (pollDelay !== null) timer = setTimeout(() => { if (!stopped) void loader.load(media); }, pollDelay);
    if (!ready) {
      node('[data-overview-state]').textContent = next.status === 'error' ? 'Statistics are unavailable. A retry is scheduled.' : 'Preparing statistics…';
      return;
    }
    const data = next.overview!;
    root.dataset.media = data.media_type;
    headlineValues(data).forEach((metric, index) => {
      const value = node(`[data-headline-value="${index}"]`);
      const changed = value.textContent !== metric.value;
      value.textContent = metric.value;
      if (changed && !initial && section === 'overview') { value.setAttribute('data-motion-fade',''); reveal(value); }
      value.parentElement!.title = metric.detail;
      node(`[data-headline-label="${index}"]`).textContent = metric.label;
    });
    node('[data-chart-section="episode_counts"]').hidden = data.media_type === 'movie';
    if (section === 'overview') {
      chartKeys.forEach(key => { if (!(key === 'episode_counts' && data.media_type === 'movie')) drawMetric(data, key, initial); });
      (['statuses', 'formats', 'countries'] as const).forEach(key => drawDistribution(data, key, initial));
    }
    // Hidden sections already have server-rendered cards. Enhance only the
    // section being viewed; switching sections uses the current snapshot.
    if (section === 'genres') genres.render(data, !initial);
    if (section === 'actors') actors.render(data, !initial);
    if (section === 'studios') studios.render(data, !initial);
    if (section === 'staff') staff.render(data, !initial);
    if (wasPending && !initial) reveal(node('.overview-results'));
    syncLinks();
    node('[data-overview-announcement]').textContent = `${data.media_type === 'all' ? 'All media' : data.media_type === 'movie' ? 'Movie' : 'Series'} statistics loaded.`;
  }
  const loader = createOverviewLoader(root.dataset.username!, {
    render: next => {
      const changedScope = next.overview && next.overview.media_type !== response.overview?.media_type;
      if (changedScope && section !== 'overview') void switchPanel(() => {
        if (next.overview?.media_type === media) render(next, true);
      });
      else render(next);
    },
    onBusy: busy => root.setAttribute('aria-busy', String(busy)),
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
      const url = new URL(location.href); url.searchParams.set('media', link.dataset.overviewMedia!);
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
    const previous = section;
    section = next === 'genres' || next === 'actors' || next === 'studios' || next === 'staff' ? next : 'overview'; root.dataset.section = section;
    node('[data-stats-heading]').textContent = section === 'staff' ? 'Staff' : section === 'studios' ? 'Studios' : section === 'actors' ? 'Actors' : section === 'genres' ? 'Genres' : 'Overview';
    root.querySelectorAll<HTMLButtonElement>('.profile-section-selector__option').forEach(button => {
      const selected = button.dataset.section === section; button.classList.toggle('is-selected', selected); button.setAttribute('aria-pressed', String(selected));
    });
    if (push) { const url = new URL(location.href); url.searchParams.set('section', section); history.pushState({}, '', url); }
    syncLinks();
    const update = () => {
      root.querySelectorAll<HTMLElement>('[data-stats-panel]').forEach(panel => panel.hidden = panel.dataset.statsPanel !== section);
      if (response.overview) render(response, true);
    };
    if (previous !== section) void switchPanel(update); else update();
  }
  root.addEventListener('profile-section-change', event => selectSection((event as CustomEvent).detail.value, true), { signal: events.signal });
  root.addEventListener('statistics-url-change', syncLinks, { signal: events.signal });
  function navigate(next: MediaScope, push: boolean) {
    tooltips.forEach(tooltip => tooltip.dismiss());
    pies.forEach(chart => { chart.tooltip?.setActiveElements([], {x:0,y:0}); chart.setActiveElements([]); chart.draw(); });
    media = next;
    clearTimeout(timer);
    if (push) { const url = new URL(location.href); url.searchParams.set('media', media); history.pushState({}, '', url); }
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
    if (!response.owner) { await loader.load(media); return; }
    if (refreshing) return;
    refreshing = true;
    const button = node<HTMLButtonElement>('[data-overview-retry]');
    button.disabled = true;
    button.textContent = 'Refreshing…';
    try {
      await refreshStatistics(events.signal);
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
  window.addEventListener('popstate', () => {
    const params = new URL(location.href).searchParams, value = params.get('media');
    root.dataset.genreSort = genreSort(params.get('sort')); root.dataset.actorSort = genreSort(params.get('actor_sort'));
    root.dataset.studioSort = genreSort(params.get('studio_sort'));
    root.dataset.staffSort = genreSort(params.get('staff_sort'));
    root.dataset.actorArtwork = actorArtwork(params.get('artwork')); selectSection(params.get('section') || 'overview', false);
    navigate(value === 'movie' || value === 'series' ? value : 'all', false);
  }, { signal: events.signal });
  reducedMotion.addEventListener('change', () => { cancelUiMotion(root); if (response.overview) render(response, true); }, { signal: events.signal });
  const observer = new MutationObserver(() => { if (response.overview) render(response, true); });
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
  // The profile runtime applies the owner/viewer accent on body, including
  // saved choices restored during client-side navigation. Repaint canvases too.
  observer.observe(document.body, { attributes: true, attributeFilter: ['style'] });
  document.addEventListener('astro:before-swap', () => { stopped = true; clearTimeout(timer); loader.stop(); events.abort(); genres.stop(); actors.stop(); studios.stop(); staff.stop(); observer.disconnect(); charts.forEach(chart => chart.destroy()); pies.forEach(chart => chart.destroy()); }, { once: true, signal: events.signal });
  render(response, true);
}
function setup() { const root = document.querySelector<HTMLElement>('#statistics-overview'); if (root) mount(root); }
document.addEventListener('astro:page-load', setup);
setup();
