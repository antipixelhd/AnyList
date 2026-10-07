const filters = ["q", "genres", "tags", "start", "end", "status", "provider"];

export function isCategoryView(values: URLSearchParams) {
  return (
    (!values.get("sort") || values.get("sort") === "all") &&
    !filters.some((name) => values.get(name)?.trim())
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
