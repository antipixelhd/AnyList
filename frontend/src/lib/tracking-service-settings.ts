interface TrackingService {
  id: string;
  name: string;
  credentials: readonly string[];
  preferences: readonly string[];
  interval?: boolean;
}

const services: readonly TrackingService[] = [
  {
    id: "trakt", name: "Trakt", credentials: ["client_id", "client_secret"], interval: true,
    preferences: ["sync_watched", "sync_ratings", "sync_lists", "sync_dropped", "watchlist_split",
      "push_watched", "push_ratings", "push_collection", "push_lists", "push_dropped", "scrobble"],
  },
  {
    id: "simkl", name: "Simkl", credentials: ["client_id"], interval: true,
    preferences: ["sync_watched", "sync_ratings", "sync_lists", "push_watched", "push_ratings", "scrobble"],
  },
  {
    id: "mdblist", name: "MDBList", credentials: ["api_key"], interval: true,
    preferences: ["sync_watched", "sync_ratings", "sync_watchlist", "sync_dropped",
      "push_watched", "push_ratings", "push_watchlist", "push_collection", "push_dropped", "scrobble"],
  },
  {
    id: "bingebase", name: "Bingebase", credentials: ["webhook_url", "api_key"],
    preferences: ["scrobble", "push_watched", "push_ratings"],
  },
];

/** Read only the selected service's explicit settings; absent checkboxes remain omitted. */
export function readTrackingServiceSettings(action: string | null, scope: ParentNode = document) {
  const service = services.find(({ id }) => action === `save_${id}_settings`);
  if (!service) return undefined;
  const body: Record<string, string | number | boolean | null> = {};
  for (const field of service.credentials) {
    const key = `${service.id}_${field}`;
    body[key] = scope.querySelector<HTMLInputElement>(`#${key}`)?.value || null;
  }
  if (service.interval) {
    const key = `${service.id}_auto_sync_interval`;
    const value = scope.querySelector<HTMLSelectElement>(`[name="${key}"]`)?.value;
    body[key] = value ? parseFloat(value) : null;
  }
  for (const field of service.preferences) {
    const key = `${service.id}_${field}`;
    const checkbox = scope.querySelector<HTMLInputElement>(`#${key}`);
    if (checkbox) body[key] = checkbox.checked;
  }
  return { name: service.name, body };
}
