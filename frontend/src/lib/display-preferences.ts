type DisplayPreferences = {
  blur_explicit?: boolean | null;
  time_format_24h?: boolean | null;
};

/** A failed settings lookup must leave existing browser preferences untouched. */
export function displayPreferenceCookies(settings: DisplayPreferences | undefined): [string, string][] {
  if (!settings) return [];
  return [
    ["blur_explicit", String(settings.blur_explicit !== false)],
    ["time_format_24h", String(settings.time_format_24h !== false)],
  ];
}
