import { responsiveArtwork } from "./responsive-artwork";

const BACKEND_PORT = (process.env.BACKEND_PORT as string | undefined) ?? "7331";
const BASE = `http://127.0.0.1:${BACKEND_PORT}`;

type ParamValue = string | number | boolean | undefined;

async function request<T>(
  path: string,
  method: string = "GET",
  params?: Record<string, ParamValue | ParamValue[]>,
  body?: unknown,
  token?: string
): Promise<T> {
  const url = new URL(path.startsWith("/") ? path.slice(1) : path, BASE.endsWith("/") ? BASE : BASE + "/");
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v === undefined) return;
      if (Array.isArray(v)) {
        v.forEach((item) => { if (item !== undefined) url.searchParams.append(k, String(item)); });
      } else {
        url.searchParams.set(k, String(v));
      }
    });
  }

  const headers: Record<string, string> = {};
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  let finalBody: BodyInit | undefined;
  if (body instanceof FormData) {
    finalBody = body;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    finalBody = JSON.stringify(body);
  }

  const res = await fetch(url.toString(), {
    method,
    headers,
    body: finalBody,
  });

  if (!res.ok) {
    let errorDetail = "";
    try {
      const errorJson = await res.json();
      errorDetail = errorJson.detail || JSON.stringify(errorJson);
    } catch (e) {}
    throw new Error(`API ${res.status}: ${path} ${errorDetail}`);
  }
  return res.json();
}

async function get<T>(path: string, params?: Record<string, ParamValue | ParamValue[]>, token?: string): Promise<T> {
  return request<T>(path, "GET", params, undefined, token);
}

async function post<T>(path: string, body?: unknown, token?: string): Promise<T> {
  return request<T>(path, "POST", undefined, body, token);
}

// Shared sub-types

export interface CastMember {
  tmdb_id: number;
  name: string;
  character: string;
  profile_path: string | null;
}

export interface ProductionCompany {
  id: number;
  name: string;
  logo_path: string | null;
}

export interface SeasonState {
  watched: boolean;
  in_library: boolean;
  collection_pct: number;
  watch_pct: number;
  watch_started?: boolean;
  user_rating: number | null;
}

export interface PersonCredit {
  tmdb_id: number;
  type: "movie" | "series";
  title: string;
  poster_path: string | null;
  release_date: string | null;
  character: string | null;
  watched?: boolean;
  in_lists?: number[];
  in_library?: boolean;
  collection_pct?: number;
}

export interface PersonDetail {
  tmdb_id: number;
  name: string;
  profile_path: string | null;
  known_for_department: string | null;
  biography: string | null;
  birthday: string | null;
  place_of_birth: string | null;
  credits: PersonCredit[];
  total_credits: number;
  page: number;
  page_size: number;
  in_lists: number[];
  collection: "in" | "out" | null;
}

export interface WatchEvent {
  id: number;
  media: MediaItem;
  user_id: number;
  watched_at: string | null;
  completed: boolean;
  progress_percent: number | null;
}

export interface SyncJob {
  id: number;
  connection_id: number | null;
  job_type: string;
  current_step: string | null;
  source: string;
  status: string;
  total_items: number;
  processed_items: number;
  error_message: string | null;
  stats?: Record<string, unknown> | null;
  updated_at: string;
}

export interface ShowSeasonOverride {
  id: number;
  source_show_tmdb_id: number;
  source_season_number: number;
  source_show_title: string | null;
  target_show_tmdb_id: number | null;
  target_show_tvdb_id: number | null;
  target_source: "tmdb" | "tvdb";
  target_season_number: number;
  target_show_title: string | null;
}

// Main types

export type MediaType = "movie" | "series" | "episode" | "person" | "collection" | "network" | "studio";

export interface UserProfile {
  id: number;
  email: string;
  username: string;
  display_name: string;
  role: string;
  is_admin: boolean;
  api_key: string;
  totp_enabled: boolean;
  email_confirmed: boolean;
  has_password: boolean;
  created_at: string;
}

export interface AdminUser {
  id: number;
  username: string;
  email: string;
  is_admin: boolean;
  api_key: string;
  created_at: string;
  avatar_url: string | null;
}

export interface GlobalSettings {
  tmdb_api_key: string | null;
  mdblist_api_key: string | null;
  tvdb_api_key: string | null;
  tvdb_subscriber_pin: string | null;
  radarr_url: string | null;
  radarr_token: string | null;
  radarr_root_folder: string | null;
  radarr_quality_profile: number | null;
  radarr_tags: number[] | null;
  sonarr_url: string | null;
  sonarr_token: string | null;
  sonarr_root_folder: string | null;
  sonarr_quality_profile: number | null;
  sonarr_tags: number[] | null;
  sonarr_season_folder: boolean;
  radarr_require_approval: boolean;
  sonarr_require_approval: boolean;
  radarr_customize_on_add: boolean;
  sonarr_customize_on_add: boolean;
  image_cache_enabled: boolean;
  image_cache_limit_gb: number | null;
  enable_logged_out_navigation: boolean;
  disable_comments: boolean;
  show_anime: boolean;
}

export interface MediaRequestItem {
  id: number;
  tmdb_id: number;
  media_type: string;
  title: string;
  poster_path: string | null;
  status: "pending" | "approved" | "rejected";
  reviewed_by: number | null;
  created_at: string;
  updated_at: string;
  user: { id: number; username: string; display_name: string };
}

export interface LoginResponse {
  access_token: string | null;
  token_type: string;
  requires_2fa: boolean;
  temp_token: string | null;
}

export interface DevicePending {
  client_name: string;
  scope: string;
  requested_at: string;
}

export interface DeviceGrant {
  id: number;
  client_name: string;
  scope: string;
  created_at: string;
  approved_at: string | null;
  last_seen_at: string | null;
}

export interface TotpBackupCode {
  id: number;
  code: string;
  used: boolean;
}

export interface TotpBackupCodesResponse {
  codes: TotpBackupCode[];
}

export interface OidcConfig {
  enabled: boolean;
  provider_name: string;
  disable_password_login: boolean;
}

export interface OidcAuthorizeResponse {
  auth_url: string;
  state: string;
}

export interface OidcExchangeResponse {
  access_token: string;
}

export type PrivacyLevel = "public" | "friends_only" | "private";

export interface PersonalDataSelection {
  watched: boolean;
  ratings: boolean;
  collection: boolean;
  lists: boolean;
  comments: boolean;
  api_keys: boolean;
  media_connections: boolean;
  scrobble_connections: boolean;
  connections: boolean;
}

export interface UserPreferences {
  background_url?: string | null;
  profile_color: string;
  apply_site_wide: boolean;
  bio: string | null;
  country: string | null;
  movie_genres: string[];
  show_genres: string[];
  disliked_genres: string[];
  streaming_services: string[];
  content_language: string | null;
  metadata_language: string | null;
  privacy_level: PrivacyLevel;
  avatar_url: string | null;
}

export interface UserSettings {
  tmdb_api_key: string | null;
  rpdb_api_key: string | null;
  has_rpdb_key: boolean;
  has_effective_tmdb_key: boolean;
  has_global_tmdb_key: boolean;
  tvdb_api_key: string | null;
  tvdb_subscriber_pin: string | null;
  has_global_tvdb_key: boolean;
  has_effective_tvdb_key: boolean;
  radarr_url: string | null;
  radarr_token: string | null;
  radarr_root_folder: string | null;
  radarr_quality_profile: number | null;
  radarr_tags: number[] | null;
  radarr_customize_on_add: boolean | null;
  has_effective_radarr: boolean;
  sonarr_url: string | null;
  sonarr_token: string | null;
  sonarr_root_folder: string | null;
  sonarr_quality_profile: number | null;
  sonarr_tags: number[] | null;
  sonarr_season_folder: boolean | null;
  sonarr_customize_on_add: boolean | null;
  has_effective_sonarr: boolean;
  trakt_client_id: string | null;
  trakt_client_secret: string | null;
  trakt_connected: boolean | null;
  trakt_sync_watched: boolean | null;
  trakt_sync_ratings: boolean | null;
  trakt_sync_lists: boolean | null;
  trakt_sync_dropped: boolean | null;
  trakt_watchlist_split: boolean | null;
  trakt_push_watched: boolean | null;
  trakt_push_ratings: boolean | null;
  trakt_push_collection: boolean | null;
  trakt_push_dropped: boolean | null;
  trakt_push_lists: boolean | null;
  trakt_scrobble: boolean | null;
  trakt_auto_sync_interval: number | null;
  trakt_auto_push_interval: number | null;
  simkl_client_id: string | null;
  simkl_connected: boolean | null;
  simkl_sync_watched: boolean | null;
  simkl_sync_ratings: boolean | null;
  simkl_sync_lists: boolean | null;
  simkl_push_watched: boolean | null;
  simkl_push_ratings: boolean | null;
  simkl_scrobble: boolean | null;
  simkl_auto_sync_interval: number | null;
  simkl_auto_push_interval: number | null;
  mdblist_api_key: string | null;
  mdblist_connected: boolean | null;
  mdblist_sync_watched: boolean | null;
  mdblist_sync_ratings: boolean | null;
  mdblist_sync_watchlist: boolean | null;
  mdblist_sync_dropped: boolean | null;
  mdblist_push_watched: boolean | null;
  mdblist_push_ratings: boolean | null;
  mdblist_push_watchlist: boolean | null;
  mdblist_push_collection: boolean | null;
  mdblist_push_dropped: boolean | null;
  mdblist_scrobble: boolean | null;
  mdblist_auto_sync_interval: number | null;
  mdblist_auto_push_interval: number | null;
  bingebase_webhook_url: string | null;
  bingebase_api_key: string | null;
  bingebase_connected: boolean | null;
  bingebase_scrobble: boolean | null;
  bingebase_push_watched: boolean | null;
  bingebase_push_ratings: boolean | null;
  preferences: UserPreferences | null;
  blur_explicit: boolean | null;
  time_format_24h: boolean | null;
  use_hls_player: boolean | null;
  shuffle_next_up: boolean | null;
  minimalist_next_up: boolean | null;
  hide_watched_from_recently_added: boolean | null;
  condense_history_by_show: boolean | null;
  rate_prompt_movies: boolean | null;
  rate_prompt_episodes: boolean | null;
  duplicate_watch_window_minutes: number | null;
}

export interface MediaServerConnection {
  id: number;
  user_id: number;
  type: "jellyfin" | "emby" | "plex" | "nuvio" | "stremio" | "arvio";
  name: string;
  url: string;
  token: string;
  server_user_id: string | null;
  server_username: string | null;
  sync_collection: boolean;
  sync_watched: boolean;
  sync_ratings: boolean;
  sync_playback: boolean;
  push_watched: boolean;
  push_collection: boolean;
  push_playback: boolean;
  push_ratings: boolean;
  auto_sync_interval: number | null;
  auto_push_interval: number | null;
  watchlist_all_users: boolean;
  watchlist_monitored_users: string[] | null;
  created_at: string;
}

export interface ScrobbleConnection {
  id: number;
  user_id: number;
  type: "jellyfin" | "emby" | "plex";
  name: string;
  server_user_id: string | null;
  server_username: string | null;
  sync_collection: boolean;
  sync_watched: boolean;
  sync_playback: boolean;
  created_at: string;
}

export interface MediaItem {
  id: number | null;
  tmdb_id: number | null;
  tvdb_id?: number | null;
  type: MediaType;
  title: string;
  original_title?: string | null;
  overview?: string | null;
  poster_path: string | null;
  backdrop_path?: string | null;
  release_date?: string | null;
  tmdb_rating?: number | null;
  season_number?: number | null;
  episode_number?: number | null;
  runtime?: number | null;
  genres?: string[];
  cast?: CastMember[];
  tagline?: string | null;
  status?: string | null;
  original_language?: string | null;
  age_rating?: string | null;
  imdb_id?: string | null;
  adult?: boolean;
  // Movie-only (#319) - TMDB tags these via community-added keywords rather
  // than a dedicated field, so they're derived server-side from the movie's
  // keyword list.
  has_mid_credits_scene?: boolean;
  has_post_credits_scene?: boolean;
  show_id?: number | null;
  show_title?: string | null;
  show_tmdb_id?: number | null;
  show_tvdb_id?: number | null;
  show_poster_path?: string | null;
  show_backdrop_path?: string | null;
  // The item's own provider ids besides tmdb_id. For an episode, tvdb_id is
  // the TVDB *episode* id. A TVDB-only episode has tvdb_id and no tmdb_id;
  // actions on it go through its local `id` (media_id) instead.
  // True when this episode has no real TMDB counterpart and was enriched
  // from TVDB instead (see #101) — its season/episode numbers are TVDB's
  // raw numbers, not TMDB's, regardless of whether show_tmdb_id is set.
  tvdb_sourced?: boolean;
  // The show's episode-ordering preference (#174) and, when it's a non-aired
  // order, this item's position in that order - see lib/episodeHref.ts and
  // lib/media-format.ts's episodeCode/displaySeasonEpisode, which card and
  // link builders should go through rather than re-deriving.
  show_episode_order?: string | null;
  display_season_number?: number | null;
  display_episode_number?: number | null;
  next_up_hidden?: boolean;
  // Next Up remaining-content estimate (#170) — released unwatched episodes
  // for the show and their estimated total runtime in minutes.
  episodes_left?: number | null;
  remaining_runtime?: number | null;
  // Next Up only (#237) - the show's most recent watch (or, mid-rewatch, that
  // rewatch's own progress/start time), for interleaving this feed with
  // /continue-watching's own watched_at into one activity-sorted list.
  last_watched_at?: string | null;
  known_for_department?: string | null;
  // network / studio search results only
  logo_path?: string | null;
  origin_country?: string | null;
  in_library?: boolean;
  playable?: boolean;
  // Card action state
  watched?: boolean;
  in_lists?: number[];
  collection_pct?: number;
  watch_pct?: number;
  watch_started?: boolean;
  is_monitored?: boolean;
  request_enabled?: boolean;
  user_rating?: number | null;
  play_count?: number;
  library: {
    resolution: string;
    video_codec: string;
    audio_codec: string;
    audio_channels: string;
    audio_languages: string[];
    subtitle_languages: string[];
  } | null;
  where_to_watch?: { type: string; name: string; logo: string | null }[];
  collection?: {
    id: number;
    name: string;
    poster_path: string | null;
    backdrop_path: string | null;
    parts: MediaItem[];
  };
  production_companies?: ProductionCompany[];
  recommendations?: MediaItem[];
  release_dates?: { digital?: string | null; physical?: string | null } | null;
}

export interface ContinueWatchingItem {
  id: number;
  media: MediaItem;
  user_id: number;
  watched_at: string;
  progress_seconds: number | null;
  progress_percent: number | null;
  completed: boolean;
}

export interface TvdbSeasonMeta {
  season_number: number;
  name: string;
  overview: string | null;
  tmdb_rating: number | null;
  poster_path: string | null;
  episode_count: number;
  air_date: string | null;
}

export interface ShowRewatch {
  started_at: string;
  watched: number;
  total: number;
}

export interface TvdbShow {
  id: number | null;
  tvdb_id: number;
  tmdb_id: number | null;
  type: string;
  title: string;
  original_title: string | null;
  overview: string | null;
  poster_path: string | null;
  backdrop_path: string | null;
  first_air_date: string | null;
  last_air_date: string | null;
  status: string | null;
  tagline: null;
  tmdb_rating: number | null;
  age_rating: string | null;
  original_language: string | null;
  imdb_id: string | null;
  tmdb_id_cross: number | null;
  episode_order: "tvdb";
  genres: string[];
  network: string | null;
  // TMDB networks (id + logo) when the show has a TMDB counterpart; the
  // name-only entries ({ id: null, logo_path: null }) are the TVDB fallback.
  networks: { id: number | null; name: string; logo_path: string | null; origin_country: string | null }[];
  seasons: TvdbSeasonMeta[];
  seasons_meta: TvdbSeasonMeta[];
  cast: { tmdb_id: null; person_id: number | null; name: string; character: string; profile_path: string | null }[];
  in_library: boolean;
  watched: boolean;
  watch_pct?: number;
  watch_started?: boolean;
  dropped: boolean;
  in_lists: number[];
  collection_pct: number;
  is_monitored: boolean;
  request_enabled: boolean;
  request_status: string | null;
  user_rating: number | null;
  season_states: Record<number, SeasonState>;
  where_to_watch: { type: string; name: string; logo: string | null }[];
  rewatch?: ShowRewatch | null;
}

export interface ProfileWatchedItem {
  tmdb_id: number;
  media_type: string;
  title: string;
  poster_path: string | null;
  backdrop_path: string | null;
  watched_at: string;
  show_title: string | null;
  show_tmdb_id: number | null;
  show_tvdb_id: number | null;
  show_poster_path: string | null;
  season_number: number | null;
  episode_number: number | null;
}

export interface ProfileRatedItem {
  tmdb_id: number;
  media_type: string;
  title: string;
  poster_path: string | null;
  backdrop_path: string | null;
  user_rating: number;
}

export interface ProfileFollowEntry {
  id: number;
  display_name: string;
  avatar_url: string | null;
}

export interface ProfileListItem {
  id: number;
  name: string;
  description: string | null;
  privacy_level: PrivacyLevel;
  item_count: number;
  updated_at: string;
  preview_posters: { url: string; adult: boolean }[];
}

export interface ProfileCommentItem {
  id: number;
  content: string;
  media_type: string;
  tmdb_id: number;
  season_number: number | null;
  episode_number: number | null;
  title: string | null;
  poster_path: string | null;
  created_at: string;
}

export interface PublicProfile {
  background_url: string | null;
  profile_color: string;
  id: number;
  username: string;
  display_name: string;
  bio: string | null;
  country: string | null;
  movie_genres: string[];
  show_genres: string[];
  created_at: string;
  total_watched: number;
  total_collected: number;
  movies_watched: number;
  shows_watched: number;
  total_rated: number;
  avatar_url: string | null;
  recently_watched_movies: ProfileWatchedItem[];
  recently_watched_shows: ProfileWatchedItem[];
  top_rated_movies: ProfileRatedItem[];
  top_rated_shows: ProfileRatedItem[];
  recent_comments: ProfileCommentItem[];
  lists: ProfileListItem[];
  follower_count: number;
  following_count: number;
  followers: ProfileFollowEntry[];
  following: ProfileFollowEntry[];
  is_following: boolean;
}

// API calls
export const api = {
  auth: {
    login: (body: FormData) =>
      post<LoginResponse>("/auth/login", body),
    register: (body: unknown) =>
      post<UserProfile>("/auth/register", body),
    registrationStatus: () =>
      get<{ enabled: boolean; smtp_configured: boolean }>("/auth/registration-status"),
    hasUsers: () =>
      get<{ has_users: boolean }>("/auth/has-users"),
    activateEmail: (token: string) =>
      post<{ success: boolean }>(`/auth/activate/${token}`, undefined),
    forgotPassword: (email: string) =>
      post<{ message: string }>("/auth/forgot-password", { email }),
    resetPassword: (token: string, new_password: string) =>
      post<{ message: string }>(`/auth/reset-password/${token}`, { new_password }),
    me: (token: string) =>
      get<UserProfile>("/auth/me", undefined, token),
    devicePending: (userCode: string, token: string) =>
      get<DevicePending>("/auth/device/pending", { user_code: userCode }, token),
    deviceGrants: (token: string) =>
      get<DeviceGrant[]>("/auth/device/grants", undefined, token),
    getSettings: (token: string) =>
      get<UserSettings>("/auth/settings", undefined, token),
    getConnections: (token: string) =>
      get<MediaServerConnection[]>("/auth/connections", undefined, token),
    getScrobbleConnections: (token: string) =>
      get<ScrobbleConnection[]>("/auth/scrobble-connections", undefined, token),
    totp2faBackupCodes: (token: string) =>
      get<TotpBackupCodesResponse>("/auth/2fa/backup-codes", undefined, token),
    totp2faVerifyLogin: (body: { temp_token: string; code: string }) =>
      post<LoginResponse>("/auth/2fa/verify-login", body),
    oidcConfig: () =>
      get<OidcConfig>("/auth/oidc/config"),
    oidcAuthorize: () =>
      get<OidcAuthorizeResponse>("/auth/oidc/authorize"),
    oidcExchange: (code: string) =>
      post<OidcExchangeResponse>("/auth/oidc/exchange", { code }),
  },

  media: {

    get: (type: string, tmdbId: number, token?: string) =>
      get<MediaItem>(`/media/${type}/${tmdbId}`, undefined, token),

    getRecommendations: (type: string, tmdbId: number, token?: string) =>
      get<{ results: MediaItem[] }>(`/media/${type}/${tmdbId}/recommendations`, undefined, token),

    getPerson: (
      personId: number,
      page: number = 1,
      token?: string,
      filters?: { collection?: "in" | "out" | ""; genre?: string[]; year?: number[]; minRating?: string },
    ) =>
      get<PersonDetail>(`/media/person/${personId}`, {
        page,
        collection: filters?.collection || undefined,
        genre: filters?.genre?.length ? filters.genre : undefined,
        year: filters?.year?.length ? filters.year : undefined,
        min_rating: filters?.minRating || undefined,
      }, token),

    publicPosterWall: () =>
      get<{ posters: { poster_path: string; title: string | null; media_type: "movie" | "series" }[] }>("/media/public/poster-wall"),

    trendingMovies: (page: number = 1, token?: string) =>
      get<{ results: MediaItem[]; page: number; total_pages: number; total_results: number }>("/media/trending/movies", { page }, token),

    trendingShows: (page: number = 1, token?: string) =>
      get<{ results: MediaItem[]; page: number; total_pages: number; total_results: number }>("/media/trending/shows", { page }, token),
  },

  shows: {

    getRecommendations: (seriesTmdbId: number, token?: string) =>
      get<{ results: MediaItem[] }>(`/shows/${seriesTmdbId}/recommendations`, undefined, token),

    getTvdb: (tvdbId: number, token?: string) =>
      get<TvdbShow>(`/shows/tvdb/${tvdbId}`, undefined, token),
  },

  sync: {
    getSeasonOverrides: (token: string) =>
      get<ShowSeasonOverride[]>("/sync/season-overrides", undefined, token),
  },

  profile: {
    get: (token: string) =>
      get<UserPreferences>("/profile/me", undefined, token),
    getPublic: (userId: number, token?: string) =>
      get<PublicProfile>(`/profile/${userId}`, undefined, token),
    publicAccessStatus: () =>
      get<{ enable_logged_out_navigation: boolean; disable_comments: boolean }>("/profile/public-access-status"),
  },

  admin: {
    getSettings: (token: string) =>
      get<GlobalSettings>("/admin/settings", undefined, token),
    listUsers: (token: string) =>
      get<AdminUser[]>("/admin/users", undefined, token),
    getPendingCount: (token: string) =>
      get<{ pending: number }>("/admin/requests/pending-count", undefined, token),
    getRequests: (token: string) =>
      get<MediaRequestItem[]>("/admin/requests", undefined, token),
  },
};

// Route TMDB and TheTVDB images through the backend image proxy (which serves
// a locally cached copy when the admin has enabled the image cache, or 302s to
// the origin otherwise). TheTVDB artwork has no size variants, so it uses the
// synthetic "tvdb" size bucket.
export function tmdbImageUrl(path: string | null | undefined, size: string = "w500"): string | null {
  return responsiveArtwork(path, { size, route: "proxy" })?.src ?? null;
}
