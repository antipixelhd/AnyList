import type { ProfileStats } from './profile-stats-data';

// Genre identity stays the same regardless of rank or movie/series mix.
const genreColors: Record<string, string> = {
  action: '#68d639', adventure: '#9256f3', animation: '#ff9b54',
  comedy: '#f4b83f', crime: '#f16e62', documentary: '#26c9a8',
  drama: '#00aaff', family: '#80d84c', fantasy: '#ed5b78',
  history: '#e8ad63', horror: '#ed5683', music: '#ed6db7',
  mystery: '#8870f5', romance: '#f36fa5', 'science fiction': '#20c5df',
  thriller: '#ff9657', war: '#a4c94f', western: '#e9a15b',
  'tv movie': '#59a4ef', kids: '#91d83f', news: '#38b8f5',
  reality: '#d87be9', talk: '#43c9bf', soap: '#ee80a8',
  'action & adventure': '#36ce8b', 'sci-fi & fantasy': '#648ef5',
  'war & politics': '#b5c65b', supernatural: '#f673a5',
};
const aliases: Record<string, string> = { 'sci-fi': 'science fiction', 'science-fiction': 'science fiction' };

export function genreColor(genre: string): string {
  const key = genre.trim().toLowerCase();
  const known = genreColors[aliases[key] ?? key];
  if (known) return known;
  const hash = [...key].reduce((value, character) => (value * 31 + character.charCodeAt(0)) >>> 0, 0);
  return `hsl(${hash % 360} 85% 62%)`;
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
