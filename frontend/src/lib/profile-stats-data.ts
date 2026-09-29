export interface ProfileStats {
  profile: {
    id: number;
    username: string;
    display_name: string;
    bio: string | null;
    profile_color: string;
    has_avatar: boolean;
    background_url: string | null;
  };
  owner: boolean;
  following: boolean;
  follows_you: boolean;
  combine_lists: boolean;
  media_type: "movie" | "series" | "all";
  year: number | null;
  current: { total: number; statuses: Record<string, number> };
  viewing: {
    unique_titles: number;
    unique_movies: number;
    unique_episodes: number;
    unique_seasons: number;
    repeat_views: number;
    estimated_watch_minutes: number;
  };
  scores: {
    average: number | null;
    rated: number;
    distribution: { score: number; count: number }[];
  };
  genres: { genre: string; count: number }[];
  activity: { month: string; movies: number; episodes: number }[];
}
