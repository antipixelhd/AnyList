import Chart from 'chart.js/auto';
import type { ChartOptions } from 'chart.js';
import type { ProfileStats } from './profile-stats-data';
import { createProfileStatsLoader } from './profile-stats-request';
import { activityTimeline, scoreBuckets, statsHighlights, statsStatuses, watchTime } from './profile-stats-presentation';

// Navigation snapshots retain data attributes, but never retain event handlers.
const mountedRoots = new WeakSet<HTMLElement>();

function setupStats() {
  const root = document.querySelector<HTMLElement>('#profile-stats');
  if (root && !mountedRoots.has(root)) mountStats(root);
}

function mountStats(root: HTMLElement) {
  mountedRoots.add(root);
  root.dataset.statsInitialized = 'true';
  let data: ProfileStats = JSON.parse(root.dataset.initial || '{}');
  let media: ProfileStats['media_type'] = data.media_type;
  let activityChart: Chart<'line'> | undefined;
  let scoreChart: Chart<'bar'> | undefined;
  const listeners = new AbortController();
  const motionQuery = matchMedia('(prefers-reduced-motion: reduce)');
  const animations = new Set<Animation>();
  const shown = { movie: true, series: true };
  const node = <T extends HTMLElement = HTMLElement>(selector: string) => root.querySelector<T>(selector)!;
  node<HTMLSelectElement>('#stats-year').value = data.year === null ? '' : String(data.year);
  root.querySelectorAll<HTMLButtonElement>('[data-stats-media]').forEach((button, index) => {
    button.setAttribute('aria-pressed', String(button.dataset.statsMedia === media));
    if (button.dataset.statsMedia === media) node('.stats-segment').style.setProperty('--segment-index', String(index));
  });
  const text = (key: string, value: string | number) => { node(`[data-stat="${key}"]`).textContent = String(value); };
  const motion = (element: HTMLElement, frames: Keyframe[], delay = 0) => {
    if (motionQuery.matches) return;
    const control = element.animate(frames, { duration: 180, delay, easing: 'cubic-bezier(.2,.7,.2,1)' });
    animations.add(control);
    void control.finished.then(() => animations.delete(control)).catch(() => animations.delete(control));
  };
  const observer = new IntersectionObserver(entries => {
    entries.filter(entry => entry.isIntersecting).forEach((entry, index) => {
      motion(entry.target as HTMLElement, [{ opacity: .4 }, { opacity: 1 }], index * 25);
      observer.unobserve(entry.target);
    });
  }, { threshold: 0.08 });
  root.querySelectorAll<HTMLElement>('[data-stats-reveal]').forEach(element => observer.observe(element));
  motionQuery.addEventListener('change', () => {
    if (motionQuery.matches) animations.forEach(animation => animation.cancel());
    if (activityChart) activityChart.options.animation = motionQuery.matches ? false : { duration: 220 };
    if (scoreChart) scoreChart.options.animation = motionQuery.matches ? false : { duration: 220 };
    if (motionQuery.matches) {
      activityChart?.stop();
      scoreChart?.stop();
      activityChart?.update('none');
      scoreChart?.update('none');
    }
  }, { signal: listeners.signal });

  // Resolve profile CSS colors to RGB for Chart.js color interpolation and tooltips.
  const probe = document.createElement('span');
  probe.hidden = true;
  root.append(probe);
  const colorCanvas = document.createElement('canvas');
  colorCanvas.width = colorCanvas.height = 1;
  const colorContext = colorCanvas.getContext('2d')!;
  const color = (variable: string) => {
    probe.style.color = `var(${variable})`;
    colorContext.clearRect(0, 0, 1, 1);
    colorContext.fillStyle = getComputedStyle(probe).color;
    colorContext.fillRect(0, 0, 1, 1);
    const [r, g, b] = colorContext.getImageData(0, 0, 1, 1).data;
    return `rgb(${r}, ${g}, ${b})`;
  };
  const accent = color('--stats-accent'), violet = color('--stats-violet'), muted = color('--stats-muted');
  const panel = color('--panel'), border = color('--border'), bright = color('--bright');
  probe.remove();
  const font = { family: getComputedStyle(root).fontFamily, size: 11 };
  const common = {
    responsive: true,
    maintainAspectRatio: false,
    animation: motionQuery.matches ? false : { duration: 220 },
    color: muted,
    font,
    interaction: { intersect: false, mode: 'index' },
    plugins: {
      legend: { display: false },
      tooltip: { backgroundColor: panel, titleColor: bright, bodyColor: bright, borderColor: border, borderWidth: 1, padding: 12, cornerRadius: 8, titleFont: { ...font, size: 12 }, bodyFont: { ...font, size: 12 }, usePointStyle: true },
    },
  } satisfies ChartOptions<'bar' | 'line'>;
  const monthLabel = (month: string, full = false) => new Intl.DateTimeFormat('en', {
    month: full ? 'long' : 'short', year: full ? 'numeric' : '2-digit', timeZone: 'UTC',
  }).format(new Date(`${month}-01T00:00:00Z`));
  function tableRows(selector: string, values: (string | number)[][]) {
    node(selector).replaceChildren(...values.map(values => {
      const row = document.createElement('tr');
      values.forEach((value, index) => {
        const cell = document.createElement(index === 0 ? 'th' : 'td');
        if (cell instanceof HTMLTableCellElement && index === 0) cell.scope = 'row';
        cell.textContent = String(value);
        row.append(cell);
      });
      return row;
    }));
  }
  function render(next: ProfileStats, updating = false) {
    data = next;
    root.dataset.initial = JSON.stringify(data);
    const highlights = statsHighlights(data), time = watchTime(data.viewing.estimated_watch_minutes);
    const period = data.year === null ? 'All time' : String(data.year);
    root.dataset.statsMedia = data.media_type;
    for (const key of ['titles', 'episodes', 'seasons', 'repeats', 'average', 'rated', 'current'] as const) text(key, highlights[key]);
    text('title-detail', data.media_type === 'movie' ? highlights.movies : data.media_type === 'series' ? highlights.series : `${highlights.movies} · ${highlights.series}`);
    text('titles-label', data.media_type === 'movie' ? 'Movies viewed' : data.media_type === 'series' ? 'Series viewed' : 'Viewed titles');
    text('time-value', time.value.toLocaleString());
    text('time-unit', time.unit);
    text('time-detail', time.detail);
    text('completion', `${highlights.completion}%`);
    for (const status of statsStatuses) {
      const row = node(`[data-status="${status.key}"]`);
      const count = data.current.statuses[status.key] || 0;
      row.querySelector('dd')!.textContent = count.toLocaleString();
      row.querySelector('.stats-status-percent')!.textContent = `${data.current.total ? Math.round(count / data.current.total * 100) : 0}%`;
      row.querySelector<HTMLElement>('.stats-status-track i')!.style.width = `${data.current.total ? count / data.current.total * 100 : 0}%`;
    }
    node('[data-genres]').replaceChildren(...data.genres.slice(0, 8).map((genre, index) => {
      const row = document.createElement('li');
      const rank = document.createElement('span');
      rank.className = 'stats-genre-rank';
      rank.textContent = String(index + 1).padStart(2, '0');
      const content = document.createElement('div');
      const label = document.createElement('div');
      label.className = 'stats-genre-label';
      const title = document.createElement('span');
      title.textContent = genre.genre;
      const count = document.createElement('strong');
      count.textContent = genre.count.toLocaleString();
      label.append(title, count);
      const track = document.createElement('span');
      track.className = 'stats-genre-track';
      track.setAttribute('aria-hidden', 'true');
      const bar = document.createElement('i');
      bar.style.width = `${data.current.total ? genre.count / data.current.total * 100 : 0}%`;
      track.append(bar);
      content.append(label, track);
      row.append(rank, content);
      return row;
    }));
    node('[data-genres-empty]').hidden = data.genres.length > 0;
    node('[data-scores-empty]').hidden = data.scores.rated > 0;

    const timeline = activityTimeline(data.activity, data.year);
    const hasActivity = data.activity.some(item => item.movies > 0 || item.episodes > 0);
    node('[data-activity-empty]').hidden = hasActivity;
    node('[data-activity-range]').textContent = timeline.length ? `${monthLabel(timeline[0].month)} — ${monthLabel(timeline[timeline.length - 1].month)}` : period;
    tableRows('[data-activity-table]', timeline.map(item => [monthLabel(item.month, true), item.movies, item.episodes]));
    const buckets = scoreBuckets(data.scores.distribution);
    tableRows('[data-scores-table]', buckets.map(item => [item.range, item.count]));
    const activityCanvas = node<HTMLCanvasElement>('#activity-chart');
    activityCanvas.setAttribute('aria-label', `Viewing activity. ${timeline.reduce((sum, item) => sum + item.movies, 0).toLocaleString()} movie plays and ${timeline.reduce((sum, item) => sum + item.episodes, 0).toLocaleString()} episode plays. Monthly values are available in Data tables.`);
    node('#score-chart').setAttribute('aria-label', `Score distribution. ${highlights.rated}, average ${highlights.average}. Score ranges are available in Data tables.`);

    if (!activityChart) {
      activityChart = new Chart<'line'>(activityCanvas, {
        type: 'line',
        data: { labels: timeline.map(item => item.month), datasets: [
          { label: 'Movies', data: timeline.map(item => item.movies), borderColor: accent, backgroundColor: accent, fill: false, tension: .25, borderWidth: 2, pointRadius: timeline.length === 1 ? 4 : 0, pointHoverRadius: 4, pointHitRadius: 16, pointBackgroundColor: accent },
          { label: 'Episodes', data: timeline.map(item => item.episodes), borderColor: violet, backgroundColor: violet, fill: false, tension: .25, borderWidth: 2, pointRadius: timeline.length === 1 ? 4 : 0, pointHoverRadius: 4, pointHitRadius: 16, pointBackgroundColor: violet },
        ] },
        options: { ...common, plugins: { ...common.plugins, tooltip: { ...common.plugins?.tooltip, callbacks: { title: items => timelineMonth(items[0]?.dataIndex), label: item => ` ${item.dataset.label}: ${Number(item.raw).toLocaleString()}` } } }, scales: {
          x: { border: { display: false }, grid: { display: false }, ticks: { color: muted, font, maxTicksLimit: 7, maxRotation: 0, callback: (_value, index) => {
            const months = activityChart?.data.labels as string[] | undefined;
            if (!months?.[index]) return '';
            return months.length > 12 ? monthLabel(months[index]) : new Intl.DateTimeFormat('en', { month: 'short', timeZone: 'UTC' }).format(new Date(`${months[index]}-01T00:00:00Z`));
          } } },
          y: { beginAtZero: true, border: { display: false }, grid: { color: border, tickLength: 0 }, ticks: { color: muted, font, precision: 0, maxTicksLimit: 5, padding: 10 } },
        } },
      });
    }
    activityChart.data.labels = timeline.map(item => item.month);
    activityChart.data.datasets[0].data = timeline.map(item => item.movies);
    activityChart.data.datasets[1].data = timeline.map(item => item.episodes);
    activityChart.data.datasets.forEach(dataset => { dataset.pointRadius = timeline.length === 1 ? 4 : 0; });
    for (const [index, type] of (['movie', 'series'] as const).entries()) {
      const button = node<HTMLButtonElement>(`[data-chart-series="${type}"]`);
      button.hidden = data.media_type !== 'all' && data.media_type !== type;
      button.setAttribute('aria-pressed', String(shown[type]));
      activityChart.setDatasetVisibility(index, shown[type] && (data.media_type === 'all' || data.media_type === type));
    }
    activityChart.update();
    if (!scoreChart) {
      scoreChart = new Chart<'bar'>(node<HTMLCanvasElement>('#score-chart'), {
        type: 'bar',
        data: { labels: buckets.map(item => item.score), datasets: [{ data: buckets.map(item => item.count), backgroundColor: accent, hoverBackgroundColor: bright, borderRadius: 4, maxBarThickness: 24 }] },
        options: { ...common, interaction: { intersect: false, mode: 'nearest', axis: 'x' }, plugins: { ...common.plugins, tooltip: { ...common.plugins?.tooltip, callbacks: {
          title: items => buckets[items[0]?.dataIndex]?.range || '',
          label: item => ` ${Number(item.raw).toLocaleString()} ${Number(item.raw) === 1 ? 'title' : 'titles'}`,
        } } }, scales: {
          x: { border: { display: false }, grid: { display: false }, ticks: { color: muted, font, autoSkip: false } },
          y: { beginAtZero: true, border: { display: false }, grid: { color: border, tickLength: 0 }, ticks: { color: muted, font, precision: 0, maxTicksLimit: 4, padding: 10 } },
        } },
      });
    }
    scoreChart.data.datasets[0].data = buckets.map(item => item.count);
    scoreChart.update();
    if (updating) {
      root.querySelectorAll<HTMLElement>('[data-stat]').forEach(element => motion(element, [{ opacity: .4 }, { opacity: 1 }]));
      root.querySelectorAll<HTMLElement>('.stats-genre-track i').forEach(element => motion(element, [{ transform: 'scaleX(.6)' }, { transform: 'scaleX(1)' }]));
      node('[data-stats-announcement]').textContent = `${data.media_type === 'all' ? 'All media' : data.media_type === 'movie' ? 'Movies' : 'Series'}, ${period}. Statistics updated.`;
    }
  }
  function timelineMonth(index: number | undefined) {
    const month = index === undefined ? undefined : activityChart?.data.labels?.[index];
    return typeof month === 'string' ? monthLabel(month, true) : '';
  }
  const error = node('.stats-error');
  const loader = createProfileStatsLoader(root.dataset.username!, {
    render: next => render(next, true),
    onError(message) {
      error.textContent = `${message} The previous statistics are still displayed.`;
      error.hidden = false;
    },
    onBusy(busy) {
      if (busy) { error.hidden = true; root.setAttribute('aria-busy', 'true'); }
      else root.removeAttribute('aria-busy');
    },
  });
  const load = () => loader.load(media, node<HTMLSelectElement>('#stats-year').value);
  root.querySelectorAll<HTMLButtonElement>('[data-stats-media]').forEach((button, index) => {
    button.addEventListener('click', () => {
      const value = button.dataset.statsMedia;
      if (value !== 'all' && value !== 'movie' && value !== 'series') return;
      if (media === value && error.hidden) return;
      media = value;
      node('.stats-segment').style.setProperty('--segment-index', String(index));
      root.querySelectorAll('[data-stats-media]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
      void load();
    }, { signal: listeners.signal });
  });
  node('#stats-year').addEventListener('change', () => void load(), { signal: listeners.signal });
  root.querySelectorAll<HTMLButtonElement>('[data-chart-series]').forEach(button => button.addEventListener('click', () => {
    const type = button.dataset.chartSeries;
    if (type !== 'movie' && type !== 'series') return;
    shown[type] = !shown[type];
    button.setAttribute('aria-pressed', String(shown[type]));
    activityChart?.setDatasetVisibility(type === 'movie' ? 0 : 1, shown[type]);
    activityChart?.update();
  }, { signal: listeners.signal }));
  render(data);
  document.addEventListener('astro:before-swap', () => {
    loader.stop();
    listeners.abort();
    observer.disconnect();
    animations.forEach(animation => animation.cancel());
    activityChart?.destroy();
    scoreChart?.destroy();
    mountedRoots.delete(root);
    delete root.dataset.statsInitialized;
  }, { once: true });
}
setupStats();
document.addEventListener('astro:after-swap', setupStats);
