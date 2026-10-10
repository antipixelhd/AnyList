import type { Chart, Plugin } from 'chart.js';
import type { ChartMetric, MetricGroup } from './stats-overview-data';
import { displayNumber } from './stats-overview-data.ts';

export function metricTooltipData(key: string, row: MetricGroup) {
  const title = key === 'scores' ? `Score >${Number(row.key) - .5}–${row.key}`
    : key === 'episode_counts' ? `${row.label} episodes` : row.label;
  return {title, values:[
    {metric:'titles', label:'Titles', value:displayNumber(row.titles)},
    {metric:'hours', label:'Hours', value:displayNumber(row.minutes / 60, 1)},
    ...(key === 'scores' ? [] : [{metric:'mean_score', label:'Mean score', value:row.mean_score === null ? 'Unrated' : displayNumber(row.mean_score, 2)}]),
  ]};
}

export function createMetricTooltip(canvas: HTMLCanvasElement, key: string, signal: AbortSignal) {
  const scroller = canvas.closest<HTMLElement>('.overview-chart-scroll')!;
  const plot = scroller.parentElement!;
  const lifetime = new AbortController();
  signal.addEventListener('abort', () => lifetime.abort(), {once:true});
  const listenerOptions = {signal:lifetime.signal};
  const description = canvas.getAttribute('aria-describedby') || '';
  const tooltip = document.createElement('div');
  tooltip.className = 'overview-chart-tooltip'; tooltip.hidden = true;
  tooltip.id = `${key}-tooltip`; tooltip.setAttribute('role', 'tooltip');
  const heading = document.createElement('strong'), values = document.createElement('dl');
  tooltip.append(heading, values); plot.append(tooltip);
  canvas.tabIndex = 0;
  canvas.setAttribute('aria-describedby', `${canvas.getAttribute('aria-describedby') || ''} ${tooltip.id}`.trim());
  let rows: MetricGroup[] = [], metric: ChartMetric = 'titles', index = -1;
  let chart: Chart<'bar' | 'line'> | undefined;
  let pointerInput = false, keyboardInput = false, dragging = false, suppressClick = false;
  let drag: {id: number; x: number; scroll: number} | undefined;
  const hide = () => { tooltip.hidden = true; index = -1; };
  const show = (next: number, pointerY?: number) => {
    if (!chart || !rows[next]) { hide(); return; }
    if (next !== index || tooltip.hidden) {
      const data = metricTooltipData(key, rows[next]); heading.textContent = data.title;
      values.replaceChildren(...data.values.map(value => {
        const pair = document.createElement('div'), label = document.createElement('dt'), number = document.createElement('dd');
        pair.classList.toggle('is-active', value.metric === metric);
        label.textContent = value.label; number.textContent = value.value; pair.append(label, number); return pair;
      }));
    }
    index = next; tooltip.hidden = false;
    const canvasRect = canvas.getBoundingClientRect(), plotRect = plot.getBoundingClientRect();
    const x = chart.scales.x.getPixelForValue(next) + canvasRect.left - plotRect.left;
    const width = tooltip.offsetWidth, height = tooltip.offsetHeight;
    const left = x + width + 20 < plot.clientWidth ? x + 16 : x - width - 16;
    tooltip.style.left = `${Math.max(8, Math.min(left, plot.clientWidth - width - 8))}px`;
    tooltip.style.top = `${Math.max(8, Math.min((pointerY ?? chart.chartArea.top) + canvasRect.top - plotRect.top + 16, plot.clientHeight - height - 8))}px`;
  };
  canvas.addEventListener('focus', () => { if (!pointerInput && canvas.matches(':focus-visible')) { keyboardInput = true; scroller.scrollLeft = 0; show(0); chart?.draw(); } }, listenerOptions);
  canvas.addEventListener('blur', () => { hide(); chart?.draw(); }, listenerOptions);
  document.addEventListener('keydown', event => { if (event.key === 'Tab') pointerInput = false; }, listenerOptions);
  canvas.addEventListener('keydown', event => {
    if (!chart || !rows.length || !['ArrowLeft','ArrowRight','Home','End','Escape'].includes(event.key)) return;
    event.preventDefault();
    keyboardInput = true; pointerInput = false;
    if (event.key === 'Escape') hide();
    else show(event.key === 'Home' ? 0 : event.key === 'End' ? rows.length - 1
      : Math.max(0, Math.min(rows.length - 1, index + (event.key === 'ArrowRight' ? 1 : -1))));
    if (index >= 0) {
      const x = chart.scales.x.getPixelForValue(index);
      if (x < scroller.scrollLeft + 24 || x > scroller.scrollLeft + scroller.clientWidth - 24) {
        scroller.scrollLeft = Math.max(0, x - scroller.clientWidth / 2);
        show(index);
      }
    }
    chart.draw();
  }, listenerOptions);
  scroller.addEventListener('scroll', () => {
    if (keyboardInput && index >= 0) show(index);
    else hide();
    chart?.draw();
  }, listenerOptions);
  scroller.addEventListener('pointerdown', event => {
    pointerInput = true; keyboardInput = false; dragging = false; suppressClick = false;
    hide(); chart?.draw();
    if (event.pointerType === 'mouse' && event.button === 0 && scroller.scrollWidth > scroller.clientWidth) {
      drag = {id:event.pointerId, x:event.clientX, scroll:scroller.scrollLeft};
    }
  }, listenerOptions);
  scroller.addEventListener('pointermove', event => {
    if (!drag || drag.id !== event.pointerId) return;
    if (Math.abs(event.clientX - drag.x) > 6) {
      dragging = true; suppressClick = true; hide();
      scroller.setPointerCapture(event.pointerId);
      scroller.classList.add('is-dragging');
      scroller.scrollLeft = drag.scroll + drag.x - event.clientX;
    }
  }, listenerOptions);
  const endDrag = () => { drag = undefined; dragging = false; scroller.classList.remove('is-dragging'); };
  window.addEventListener('pointerup', endDrag, listenerOptions);
  window.addEventListener('pointercancel', endDrag, listenerOptions);
  scroller.addEventListener('click', event => {
    if (suppressClick) { event.preventDefault(); event.stopImmediatePropagation(); }
  }, {...listenerOptions, capture:true});
  document.addEventListener('pointerdown', event => {
    if (!tooltip.hidden && !plot.contains(event.target as Node)) { hide(); chart?.draw(); }
  }, listenerOptions);
  const plugin: Plugin<'bar' | 'line'> = {
    id:'overview-metric-tooltip',
    afterInit(instance) { chart = instance; },
    // Chart.update replays the last pointer event, including a stale mobile tap.
    beforeEvent(_instance, args) {
      if (!args.replay && args.event.type === 'mousemove' && !drag) suppressClick = false;
      if (args.replay || dragging || suppressClick) return false;
    },
    resize() { hide(); },
    afterEvent(instance, args) {
      if (args.replay) return;
      keyboardInput = false;
      const {x,y,type} = args.event, area = instance.chartArea;
      if (type === 'mouseout' || x === null || y === null || x < area.left || x > area.right || y < area.top || y > area.bottom) hide();
      else show(Math.round(Number(instance.scales.x.getValueForPixel(x))), y);
      args.changed = true;
    },
    afterDraw(instance) {
      if (tooltip.hidden || index < 0) return;
      const {ctx,chartArea} = instance, x = instance.scales.x.getPixelForValue(index);
      ctx.save(); ctx.strokeStyle = String(instance.data.datasets[0].borderColor); ctx.globalAlpha = .45;
      ctx.lineWidth = 1; ctx.setLineDash([3,4]); ctx.beginPath(); ctx.moveTo(x,chartArea.top); ctx.lineTo(x,chartArea.bottom); ctx.stroke(); ctx.restore();
    },
    afterDestroy() { hide(); lifetime.abort(); tooltip.remove(); canvas.setAttribute('aria-describedby',description); chart = undefined; },
  };
  return {plugin, dismiss() { hide(); chart?.setActiveElements([]); chart?.draw(); }, update(next: MetricGroup[], active: ChartMetric) { rows = next; metric = active; hide(); }};
}
