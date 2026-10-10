import Chart from 'chart.js/auto';
import type { ChartOptions } from 'chart.js';
import type { ChartMetric, MediaScope, MetricGroup, Overview, OverviewResponse } from './stats-overview-data';
import { displayNumber, distributionRows, headlineValues, knownRows, metricLabels, metricValue, watchYearRows } from './stats-overview-data';
import { createOverviewLoader } from './stats-overview-request';
import { mountGenres } from './stats-genres';
import { genreSort } from './stats-genres-data';
import { actorArtwork } from './stats-actors-data';
import { mountActors } from './stats-actors';

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
  const events = new AbortController();
  const genres = mountGenres(root);
  const actors = mountActors(root);
  let section = root.dataset.section === 'genres' || root.dataset.section === 'actors' ? root.dataset.section : 'overview';
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const motion = (initial: boolean) => reducedMotion.matches || initial ? false as const : { duration: 900, easing: 'easeInOutCubic' as const };
  const activeMotion = () => ({ active: { animation: { duration: reducedMotion.matches ? 0 : 160 } } });
  const charts = new Map<string, Chart<'bar' | 'line'>>();
  const labelColors = new Map<string, string>();
  const pies = new Map<string, Chart<'pie'>>();
  const metrics = new Map<string, ChartMetric>(chartKeys.map(key => [key, 'titles']));
  const from = node<HTMLSelectElement>('[data-watch-from]'), through = node<HTMLSelectElement>('[data-watch-through]');
  const color = (variable: string) => {
    const probe = document.createElement('span');
    probe.style.color = `var(${variable})`;
    root.append(probe);
    const result = getComputedStyle(probe).color;
    probe.remove();
    return result;
  };
  const categoryColor = (key: string) => {
    const fixed: Record<string, string> = { movie: color('--stats-cyan'), series: '#b77bdc', watching: color('--stats-cyan'), completed: '#45a987', paused: '#ed984e', dropped: '#d94d5b', planning: '#65788e', Unknown: '#65788e', US: color('--stats-cyan'), JP: '#b77bdc', GB: '#45a987' };
    const palette = ['#3b8ebf', '#b77bdc', '#45a987', '#ed984e', '#d94d5b', '#5986cb', '#b48d42', '#679e9d'];
    return fixed[key] || palette[([...key].reduce((sum, letter) => sum * 31 + letter.charCodeAt(0), 0) >>> 0) % palette.length];
  };
  function dataRows(data: Overview, key: typeof chartKeys[number]): MetricGroup[] {
    const rows = knownRows(data[key]);
    return key === 'watch_years' ? watchYearRows(rows, from.value, through.value) : rows;
  }
  function table(key: string, rows: MetricGroup[]) {
    node(`[data-chart-table="${key}"]`).replaceChildren(...rows.map(row => {
      const tr = document.createElement('tr');
      [key === 'scores' ? `(${Number(row.key) - .5}, ${row.key}]` : row.label, displayNumber(row.titles), displayNumber(row.minutes / 60, 1), displayNumber(row.mean_score, 2), displayNumber(row.rated_titles)].forEach((value, index) => {
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
    const isLine = key.includes('years');
    const cyan = color('--stats-cyan'), muted = color('--muted'), bright = color('--bright'), panel = color('--panel');
    labelColors.set(key, bright);
    const empty = rows.every(row => row.titles === 0 || (metric === 'mean_score' && row.mean_score === null));
    const message = node(`[data-chart-empty="${key}"]`);
    message.hidden = !empty;
    message.textContent = metric === 'mean_score' ? 'No rated watched titles.' : 'No watched titles.';
    canvas.setAttribute('aria-label', `${node(`[data-chart-section="${key}"] h3`).textContent}: ${metricLabels[metric]}`);
    const options: ChartOptions<'bar' | 'line'> = {
      responsive: true, maintainAspectRatio: false,
      animation: motion(initial), transitions: activeMotion(),
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { display: false }, tooltip: {
        backgroundColor: panel, titleColor: bright, bodyColor: bright, borderColor: muted, borderWidth: 1,
        callbacks: { title: items => key === 'scores' ? `Score (${Number(plotted[items[0].dataIndex]!.key) - .5}, ${plotted[items[0].dataIndex]!.key}]` : items[0].label,
          label: item => `${metricLabels[metric]}: ${displayNumber(item.parsed.y, metric === 'titles' ? 0 : 2)}`,
          afterLabel: item => metric === 'mean_score' ? `${displayNumber(plotted[item.dataIndex]!.rated_titles)} rated titles` : '', },
      } },
      scales: { x: { grid: { display: false }, border: { display: false }, ticks: { color: muted, maxRotation: 0, autoSkip: true, maxTicksLimit: key === 'scores' ? 10 : 12, font: { size: 11 } } },
        y: { beginAtZero: true, grace: '15%', max: metric === 'mean_score' ? 10 : undefined, display: false } },
      layout: { padding: { top: 10 } },
    };
    const dataset = { label: metricLabels[metric], data: plotted.map(row => row ? metricValue(row, metric) : null), borderColor: cyan, backgroundColor: cyan,
      borderWidth: isLine ? 2 : 0, borderRadius: 3, pointRadius: rows.length > 25 ? 2 : 3, pointHoverRadius: 5, tension: 0, spanGaps: false };
    const existing = charts.get(key);
    if (existing) {
      existing.data.labels = plotted.map(row => row?.label || '');
      Object.assign(existing.data.datasets[0], dataset);
      existing.options = options;
      existing.update(initial || reducedMotion.matches ? 'none' : undefined);
    } else {
      charts.set(key, new Chart<'bar' | 'line'>(canvas, { type: isLine ? 'line' : 'bar', data: { labels: plotted.map(row => row?.label || ''), datasets: [dataset] }, options,
        plugins: [{ id: 'overview-values', afterDatasetsDraw(chart) {
          const ctx = chart.ctx;
          ctx.save(); ctx.fillStyle = labelColors.get(key)!; ctx.font = '11px sans-serif'; ctx.textAlign = 'center';
          let lastRight = -Infinity;
          chart.getDatasetMeta(0).data.forEach((element, index) => {
            const value = chart.data.datasets[0].data[index];
            if (typeof value !== 'number' || value <= 0) return;
            const label = displayNumber(value, chart.data.datasets[0].label === metricLabels.mean_score ? 2 : chart.data.datasets[0].label === metricLabels.hours ? 1 : 0);
            const { x, y } = element.tooltipPosition(false), half = ctx.measureText(label).width / 2;
            if (x === null || y === null) return;
            if (x - half > lastRight + 4 && x + half < chart.width) { ctx.fillText(label, x, Math.max(y - 8, 12)); lastRight = x + half; }
          });
          ctx.restore();
        } }],
      }));
    }
    table(key, rows);
  }
  function drawDistribution(data: Overview, key: 'statuses' | 'formats' | 'countries', initial = false) {
    const rows = distributionRows(data, key);
    const labels = rows.map(row => row.label);
    const values = rows.map(row => row.share ?? row.titles);
    const colors = rows.map(row => categoryColor(row.key));
    const canvas = node<HTMLCanvasElement>(`[data-overview-distribution="${key}"]`);
    node(`[data-distribution-empty="${key}"]`).hidden = values.some(value => value > 0);
    const options: ChartOptions<'pie'> = { responsive: true, maintainAspectRatio: false,
      animation: motion(initial), transitions: activeMotion(),
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: item => `${labels[item.dataIndex]}: ${displayNumber(rows[item.dataIndex].percent, 1)}%` } } } };
    let pie = pies.get(key);
    if (!pie) { pie = new Chart<'pie'>(canvas, { type: 'pie', data: { labels, datasets: [{ data: values, backgroundColor: colors, borderWidth: 0 }] }, options }); pies.set(key, pie); }
    else { pie.data.labels = labels; Object.assign(pie.data.datasets[0], { data: values, backgroundColor: colors, borderWidth: 0 }); pie.options = options; pie.update(initial || reducedMotion.matches ? 'none' : undefined); }
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
    response = next;
    root.dataset.initial = JSON.stringify(next);
    node('.overview-error').hidden = true;
    const ready = next.status === 'ready' && next.overview !== null;
    node('.overview-results').hidden = !ready;
    node('.overview-pending').hidden = ready;
    if (!ready) {
      node('[data-overview-state]').textContent = next.status === 'error' ? 'Statistics are unavailable. A retry is scheduled.' : 'Preparing statistics…';
      clearTimeout(timer);
      timer = setTimeout(() => { if (!stopped) void loader.load(media); }, next.status === 'error' ? 30000 : 5000);
      return;
    }
    clearTimeout(timer);
    const data = next.overview!;
    root.dataset.media = data.media_type;
    headlineValues(data).forEach((metric, index) => {
      const value = node(`[data-headline-value="${index}"]`);
      value.textContent = metric.value;
      value.parentElement!.title = metric.detail;
      node(`[data-headline-label="${index}"]`).textContent = metric.label;
    });
    node('[data-chart-section="episode_counts"]').hidden = data.media_type === 'movie';
    for (const select of [from, through]) {
      const previous = select.value;
      select.replaceChildren(new Option(select === from ? 'First year' : 'Latest year', ''), ...data.watch_years.map(row => new Option(row.label, row.key)));
      select.value = data.watch_years.some(row => row.key === previous) ? previous : '';
    }
    if (section === 'overview') {
      chartKeys.forEach(key => { if (!(key === 'episode_counts' && data.media_type === 'movie')) drawMetric(data, key, initial); });
      (['statuses', 'formats', 'countries'] as const).forEach(key => drawDistribution(data, key, initial));
    }
    genres.render(data);
    actors.render(data);
    syncLinks();
    node('[data-overview-announcement]').textContent = `${data.media_type === 'all' ? 'All media' : data.media_type === 'movie' ? 'Movie' : 'Series'} statistics loaded.`;
  }
  const loader = createOverviewLoader(root.dataset.username!, { render,
    onBusy: busy => root.setAttribute('aria-busy', String(busy)),
    onError: (message, denied) => {
      const error = node('.overview-error'); error.textContent = message; error.hidden = false;
      if (denied) {
        clearTimeout(timer);
        response.overview = null;
        node('.overview-results').hidden = true;
        node('.overview-pending').hidden = true;
        root.removeAttribute('data-initial');
        root.querySelectorAll('[data-chart-table],[data-distribution-rows]').forEach(element => element.replaceChildren());
        root.querySelectorAll('[data-headline-value]').forEach(element => element.textContent = '—');
        charts.forEach(chart => chart.destroy()); charts.clear(); pies.forEach(chart => chart.destroy()); pies.clear();
        genres.render(null);
        actors.render(null);
      }
    },
  });
  function syncLinks() {
    root.querySelectorAll<HTMLAnchorElement>('[data-overview-media]').forEach(link => {
      const url = new URL(location.href); url.searchParams.set('media', link.dataset.overviewMedia!);
      url.searchParams.set('section', section); url.searchParams.set('sort', root.dataset.genreSort || 'count');
      url.searchParams.set('actor_sort', root.dataset.actorSort || 'count'); url.searchParams.set('artwork', root.dataset.actorArtwork || 'media');
      link.href = url.pathname + url.search;
    });
  }
  function selectSection(next: string, push: boolean) {
    section = next === 'genres' || next === 'actors' ? next : 'overview'; root.dataset.section = section;
    node('[data-stats-heading]').textContent = section === 'actors' ? 'Actors' : section === 'genres' ? 'Genres' : 'Overview';
    root.querySelectorAll<HTMLElement>('[data-stats-panel]').forEach(panel => panel.hidden = panel.dataset.statsPanel !== section);
    root.querySelectorAll<HTMLButtonElement>('.profile-section-selector__option').forEach(button => {
      const selected = button.dataset.section === section; button.classList.toggle('is-selected', selected); button.setAttribute('aria-pressed', String(selected));
    });
    if (push) { const url = new URL(location.href); url.searchParams.set('section', section); history.pushState({}, '', url); }
    syncLinks();
    if (response.overview) render(response, true);
  }
  root.addEventListener('profile-section-change', event => selectSection((event as CustomEvent).detail.value, true), { signal: events.signal });
  root.addEventListener('statistics-url-change', syncLinks, { signal: events.signal });
  function navigate(next: MediaScope, push: boolean) {
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
    } else if (target.hasAttribute('data-overview-retry')) void loader.load(media);
    else if (target.dataset.pie && response.overview) {
      const key = target.dataset.pie as 'statuses' | 'formats' | 'countries';
      const index = distributionRows(response.overview, key).findIndex(row => row.key === target.dataset.category);
      const chart = pies.get(key); chart?.setActiveElements([{ datasetIndex: 0, index }]); chart?.render();
    }
  }, { signal: events.signal });
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
  for (const select of [from, through]) select.addEventListener('change', () => {
    if (from.value && through.value && Number(from.value) > Number(through.value)) (select === from ? through : from).value = select.value;
    if (response.overview) drawMetric(response.overview, 'watch_years');
  }, { signal: events.signal });
  window.addEventListener('popstate', () => {
    const params = new URL(location.href).searchParams, value = params.get('media');
    root.dataset.genreSort = genreSort(params.get('sort')); root.dataset.actorSort = genreSort(params.get('actor_sort'));
    root.dataset.actorArtwork = actorArtwork(params.get('artwork')); selectSection(params.get('section') || 'overview', false);
    navigate(value === 'movie' || value === 'series' ? value : 'all', false);
  }, { signal: events.signal });
  reducedMotion.addEventListener('change', () => { if (response.overview) render(response, true); }, { signal: events.signal });
  const observer = new MutationObserver(() => { if (response.overview) render(response, true); });
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
  document.addEventListener('astro:before-swap', () => { stopped = true; clearTimeout(timer); loader.stop(); events.abort(); genres.stop(); actors.stop(); observer.disconnect(); charts.forEach(chart => chart.destroy()); pies.forEach(chart => chart.destroy()); }, { once: true, signal: events.signal });
  render(response, true);
}
function setup() { const root = document.querySelector<HTMLElement>('#statistics-overview'); if (root) mount(root); }
document.addEventListener('astro:page-load', setup);
setup();
