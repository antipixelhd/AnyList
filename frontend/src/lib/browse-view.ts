const filters = ["q", "genres", "start", "end", "status", "provider", "cinema", "min_score"];
export function browseLayout(values: URLSearchParams) { return values.get('view')==='timeline'?'timeline':'grid'; }
export function genreSelectionLabel(value: string, genres: { id: number | string; name: string }[]) {
  const selected = value.split(',').filter(Boolean);
  if (!selected.length) return 'Any';
  if (selected.length > 1) return `${selected.length} selected`;
  return genres.find(genre => String(genre.id) === selected[0])?.name || 'Selected genre';
}

export function hasBrowseFilters(values: URLSearchParams) {
  return filters.some(name => name === "min_score" ? Number(values.get(name)) > 0 : values.get(name)?.trim());
}

export function isCategoryView(values: URLSearchParams) {
  return (
    browseLayout(values)!=='timeline' && (!values.get("sort") || values.get("sort") === "all") &&
    !hasBrowseFilters(values)
  );
}

export function browseRequestParams(values: URLSearchParams, today = new Date().toISOString().slice(0,10)) {
  const request = new URLSearchParams(values);
  request.delete('view');
  if(browseLayout(values)==='timeline'){
    request.set('timeline','true');
    request.set('sort', values.get('sort') === 'upcoming' || values.get('status') === 'upcoming' ? 'upcoming' : 'newest');
    if(!request.has('end')&&request.get('sort')!=='upcoming'&&!(request.get('start')!>today))request.set('end',today);
    return request;
  }
  if (!request.get("sort") || request.get("sort") === "all")
    request.set("sort", "popular");
  if (request.get('status') === 'upcoming' && ['popular', 'trending', 'newest'].includes(request.get('sort')!)) request.set('sort', 'upcoming');
  if (request.get('sort') === 'newest' && !request.has('end') && !request.has('q')) request.set('end', today);
  return request;
}

export function sectionHref(
  type: string,
  region: string,
  filters: Record<string, string>,
) {
  const values = new URLSearchParams(filters);
  if (type === "series") values.set("type", type);
  values.set("region", region);
  return `/browse?${values}`;
}
