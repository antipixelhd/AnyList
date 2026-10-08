import type { ProfileStats } from './profile-stats-data';

// Genre identity stays the same regardless of rank or movie/series mix.
const genreColors: Record<string, string> = {
  action: '#83d85a', adventure: '#ad8aff', animation: '#ffc76b',
  comedy: '#ffd56b', crime: '#ff8b7b', documentary: '#72cbbb',
  drama: '#58bfff', family: '#9ddc81', fantasy: '#c293ff',
  history: '#d9b88a', horror: '#ee7b98', music: '#f89ac8',
  mystery: '#969eff', romance: '#ff93b9', 'science fiction': '#65d4dc',
  thriller: '#e4a36f', war: '#c2c98a', western: '#d8ad75',
  'tv movie': '#95b7dd', kids: '#ace181', news: '#8bbce8',
  reality: '#e8b5e8', talk: '#9ccac8', soap: '#eaa6bc',
  'action & adventure': '#62c996', 'sci-fi & fantasy': '#82acef',
  'war & politics': '#b9b298', supernatural: '#da8de8',
};
const aliases: Record<string, string> = { 'sci-fi': 'science fiction', 'science-fiction': 'science fiction' };

export function genreColor(genre: string): string {
  const key = genre.trim().toLowerCase();
  const known = genreColors[aliases[key] ?? key];
  if (known) return known;
  const hash = [...key].reduce((value, character) => (value * 31 + character.charCodeAt(0)) >>> 0, 0);
  return `hsl(${hash % 360} 65% 72%)`;
}

export function overviewGenres(summaries: (ProfileStats | null)[]) {
  const totals = new Map<string, { genre: string; count: number }>();
  for (const summary of summaries) {
    for (const { genre, count } of summary?.genres ?? []) {
      const name = genre.trim();
      const key = aliases[name.toLowerCase()] ?? name.toLowerCase();
      const existing = totals.get(key);
      totals.set(key, {
        genre: existing?.genre ?? (key === 'science fiction' ? 'Science Fiction' : name),
        count: (existing?.count ?? 0) + count,
      });
    }
  }
  return [...totals.values()].map(({ genre, count }) => ({ genre, count, color: genreColor(genre) }))
    .sort((a, b) => b.count - a.count || a.genre.localeCompare(b.genre));
}

export function overviewDays(minutes: number) {
  return new Intl.NumberFormat('en', { maximumFractionDigits: 1 }).format(minutes / 1440);
}
