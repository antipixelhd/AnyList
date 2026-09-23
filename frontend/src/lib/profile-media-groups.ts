type ProfileMedia = {type: string};

const mediaLabels: Record<string, string> = {
  movie: 'Movies',
  series: 'Series',
  book: 'Books',
  game: 'Games',
};

export function profileMediaGroups<T extends ProfileMedia>(items: T[], combineLists: boolean) {
  const groups: {type: string; heading: string; items: T[]}[] = [];
  const movieSeries = items.filter(item => item.type === 'movie' || item.type === 'series');
  if (combineLists) {
    if (movieSeries.length) groups.push({type: 'combined', heading: 'Movie/Series', items: movieSeries});
  } else {
    for (const type of ['movie', 'series']) {
      const matching = items.filter(item => item.type === type);
      if (matching.length) groups.push({type, heading: mediaLabels[type], items: matching});
    }
  }
  const otherTypes = [...new Set(items.map(item => item.type).filter(type => type !== 'movie' && type !== 'series'))];
  for (const type of otherTypes) {
    const heading = mediaLabels[type] ?? `${type.charAt(0).toUpperCase()}${type.slice(1)}s`;
    groups.push({type, heading, items: items.filter(item => item.type === type)});
  }
  return groups;
}
