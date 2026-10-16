import { formatDateTime } from './date-time.ts';
export type TimelineItem = {
  release_date?: string | null;
  year?: string;
  title: string;
};

export function releaseGroup(item: TimelineItem) {
  const match = /^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$/.exec(item.release_date || '');
  const year = match?.[1] || (/^\d{4}$/.test(item.year || '') ? item.year! : '');
  let month = Number(match?.[2] || 0), day = Number(match?.[3] || 0);
  if (month < 1 || month > 12 ||
      (day && day > new Date(Date.UTC(Number(year), month, 0)).getUTCDate())) {
    month = 0;
    day = 0;
  }
  return {
    year,
    key: `${year || 'undated'}-${String(month).padStart(2, '0')}`,
    label: month ? formatDateTime(`${year}-${String(month).padStart(2, '0')}`, 'month-name') : year ? 'Month not listed' : 'Release date not listed',
    order: year ? `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}` : '',
  };
}

export function timelineGroups<T extends TimelineItem>(items: T[]) {
  const years = new Map<string, {year: string; months: {key: string; label: string; items: T[]}[]}>();
  const dated = items.map(item => ({item, date: releaseGroup(item)}));
  dated.sort((a, b) => b.date.order.localeCompare(a.date.order) || a.item.title.localeCompare(b.item.title));
  for (const {item, date} of dated) {
    if (!years.has(date.year)) years.set(date.year, {year: date.year, months: []});
    const group = years.get(date.year)!;
    let month = group.months.find(month => month.key === date.key);
    if (!month) {
      month = {key: date.key, label: date.label, items: []};
      group.months.push(month);
    }
    month.items.push(item);
  }
  return [...years.values()];
}
