import { timelineGroups, type TimelineItem } from './browse-timeline-data';

/** Keep the existing cards and month sections when pagination crosses a boundary. */
export function appendBrowseTimeline<T extends TimelineItem>(root: HTMLElement, cards: HTMLElement[], items: Map<HTMLElement,T>) {
  const existing = [...root.querySelectorAll<HTMLElement>('[data-browse-card]')];
  const anchor = existing.find(card => {
    const bounds = card.getBoundingClientRect();
    return bounds.bottom > 0 && bounds.top < innerHeight;
  });
  const top = anchor?.getBoundingClientRect().top;
  const byItem = new Map([...existing, ...cards].map(card => [items.get(card)!, card]));
  const years = new Map([...root.querySelectorAll<HTMLElement>('[data-timeline-year]')].map(year => [year.dataset.timelineYear!, year]));
  const months = new Map([...root.querySelectorAll<HTMLElement>('[data-timeline-month]')].map(month => [month.dataset.timelineMonth!, month]));
  const place = (parent: HTMLElement, child: HTMLElement, index: number) => {
    if (parent.children[index] !== child) parent.insertBefore(child, parent.children[index] || null);
  };
  timelineGroups([...byItem.keys()].filter(Boolean)).forEach((group, index) => {
    let year = years.get(group.year);
    if (!year) {
      year = document.createElement('section');
      year.className = 'browse-timeline-year';
      year.dataset.timelineYear = group.year;
      const heading = document.createElement('h2');
      heading.className = 'browse-timeline-year-label';
      heading.textContent = group.year || 'Undated';
      const body = document.createElement('div');
      body.className = 'browse-timeline-months';
      year.append(heading, body);
    }
    place(root, year, index);
    const body = year.querySelector<HTMLElement>('.browse-timeline-months')!;
    group.months.forEach((group, index) => {
      let month = months.get(group.key);
      if (!month) {
        month = document.createElement('section');
        month.className = 'browse-timeline-month';
        month.dataset.timelineMonth = group.key;
        const heading = document.createElement('h3');
        heading.textContent = group.label;
        const grid = document.createElement('div');
        grid.className = 'browse-grid';
        month.append(heading, grid);
      }
      place(body, month, index);
      const grid = month.querySelector<HTMLElement>('.browse-grid')!;
      group.items.forEach((item, index) => place(grid, byItem.get(item)!, index));
    });
  });
  if (anchor && top !== undefined) {
    const delta = anchor.getBoundingClientRect().top - top;
    if (Math.abs(delta) > 1) scrollBy({top: delta, behavior: 'instant'});
  }
}
