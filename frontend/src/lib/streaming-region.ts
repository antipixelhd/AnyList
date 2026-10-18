export const STREAMING_REGION_COOKIE = 'anylist_streaming_region';

// Date-format regions can group countries (Vienna and Zurich use de-DE).
// Streaming rights cannot, so each supported device zone maps to its country.
const ZONE_COUNTRIES: Record<string, string> = {
  'Europe/Berlin': 'DE', 'Europe/Busingen': 'DE',
  'Europe/Vienna': 'AT', 'Europe/Zurich': 'CH',
  'Europe/London': 'GB', 'Europe/Paris': 'FR',
  'Europe/Madrid': 'ES', 'Europe/Rome': 'IT',
  'Europe/Amsterdam': 'NL', 'Europe/Warsaw': 'PL',
  'Europe/Lisbon': 'PT', 'Europe/Stockholm': 'SE',
  'Europe/Copenhagen': 'DK', 'Europe/Oslo': 'NO',
  'Europe/Helsinki': 'FI', 'Europe/Prague': 'CZ',
  'America/New_York': 'US', 'America/Chicago': 'US',
  'America/Denver': 'US', 'America/Los_Angeles': 'US',
  'America/Toronto': 'CA', 'America/Vancouver': 'CA',
  'America/Sao_Paulo': 'BR', 'America/Mexico_City': 'MX',
  'Australia/Sydney': 'AU', 'Australia/Melbourne': 'AU',
  'Asia/Tokyo': 'JP', 'Asia/Seoul': 'KR',
  'Asia/Shanghai': 'CN', 'Asia/Taipei': 'TW',
  'Asia/Kolkata': 'IN', 'Asia/Riyadh': 'SA',
};

export function streamingCountry(value: string | null | undefined): string | undefined {
  return value && /^[A-Z]{2}$/.test(value) ? value : undefined;
}

export function streamingRegionPreference(value: string | null | undefined): { country: string; manual: boolean } | undefined {
  const match = /^(auto|manual)\.([A-Z]{2})$/.exec(value || '');
  return match ? { country: match[2], manual: match[1] === 'manual' } : undefined;
}

export function searchStreamingCountry(accountCountry: string | null | undefined, preference: ReturnType<typeof streamingRegionPreference>, deviceCountry?: string): string | undefined {
  return (preference?.manual ? preference.country : undefined) || streamingCountry(accountCountry) || preference?.country || deviceCountry;
}

export function rememberDeviceRegion(cookie = document.cookie, detected = deviceStreamingCountry()) {
  const value = cookie.split(';').map(part => part.trim()).find(part => part.startsWith(`${STREAMING_REGION_COOKIE}=`))?.slice(STREAMING_REGION_COOKIE.length + 1);
  const saved = streamingRegionPreference(value);
  if (saved?.manual || !detected) return saved;
  if (saved?.country !== detected) document.cookie = `${STREAMING_REGION_COOKIE}=auto.${detected}; Path=/; Max-Age=31536000; SameSite=Lax${location.protocol === 'https:' ? '; Secure' : ''}`;
  return { country: detected, manual: false };
}

export function deviceStreamingCountry(timeZone?: string, languages?: readonly string[]): string | undefined {
  let zone = timeZone;
  if (zone === undefined) {
    try { zone = new Intl.DateTimeFormat().resolvedOptions().timeZone; } catch { /* Use locale below. */ }
  }
  if (zone && ZONE_COUNTRIES[zone]) return ZONE_COUNTRIES[zone];
  const candidates = languages ?? (typeof navigator !== 'undefined' ? navigator.languages : []);
  for (const language of candidates) {
    try {
      const country = streamingCountry(new Intl.Locale(language).region);
      if (country) return country;
    } catch { /* Ignore malformed browser locales. */ }
  }
  return undefined;
}
