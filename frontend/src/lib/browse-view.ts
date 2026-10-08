const filters = ["q", "genres", "start", "end", "status", "provider"];
export function genreSelectionLabel(value: string, genres: { id: number | string; name: string }[]) {
  const selected = value.split(',').filter(Boolean);
  if (!selected.length) return 'Any';
  if (selected.length > 1) return `${selected.length} selected`;
  return genres.find(genre => String(genre.id) === selected[0])?.name || 'Selected genre';
}

export function hasBrowseFilters(values: URLSearchParams) {
  return filters.some(name => values.get(name)?.trim());
}

export function isCategoryView(values: URLSearchParams) {
  return (
    (!values.get("sort") || values.get("sort") === "all") &&
    !hasBrowseFilters(values)
  );
}

export function browseRequestParams(values: URLSearchParams) {
  const request = new URLSearchParams(values);
  if (!request.get("sort") || request.get("sort") === "all")
    request.set("sort", "popular");
  return request;
}

export function sectionHref(
  type: string,
  region: string,
  filters: Record<string, string>,
) {
  const values = new URLSearchParams(filters);
  if (type === "series") values.set("type", type);
  if (region !== "US") values.set("region", region);
  return `/browse?${values}`;
}
