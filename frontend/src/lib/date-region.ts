/** A browser preference, independent of metadata language and release country. */
export const DATE_REGION_COOKIE = 'anylist_date_region';
export const DATE_REGIONS = [
  ['de-DE', 'Germany'], ['en-GB', 'United Kingdom'], ['en-US', 'United States'],
  ['fr-FR', 'France'], ['es-ES', 'Spain'], ['it-IT', 'Italy'],
  ['nl-NL', 'Netherlands'], ['pl-PL', 'Poland'], ['pt-PT', 'Portugal'],
  ['pt-BR', 'Brazil'], ['sv-SE', 'Sweden'], ['da-DK', 'Denmark'],
  ['nb-NO', 'Norway'], ['fi-FI', 'Finland'], ['cs-CZ', 'Czechia'],
  ['en-CA', 'Canada'], ['en-AU', 'Australia'], ['ja-JP', 'Japan'],
  ['ko-KR', 'South Korea'], ['zh-CN', 'China'], ['zh-TW', 'Taiwan'],
  ['hi-IN', 'India'], ['ar-SA', 'Saudi Arabia'],
] as const;

export function dateRegion(value: string | null | undefined): string | undefined {
  return DATE_REGIONS.some(([locale]) => locale === value) ? value! : undefined;
}

export function browserDateRegion(): string | undefined {
  if (typeof document === 'undefined') return undefined;
  const entry = document.cookie.split(';').map(part => part.trim()).find(part => part.startsWith(`${DATE_REGION_COOKIE}=`));
  return dateRegion(entry?.slice(DATE_REGION_COOKIE.length + 1));
}

// The browser exposes its time zone, but not the operating system's short-date
// pattern. Use unambiguous, supported zones as a local hint when no region was
// chosen explicitly; unknown zones fall back to the browser locale.
const DEVICE_REGIONS: Record<string, string> = {
  'Europe/Berlin': 'de-DE', 'Europe/Busingen': 'de-DE',
  'Europe/Vienna': 'de-DE', 'Europe/Zurich': 'de-DE',
  'Europe/London': 'en-GB', 'Europe/Paris': 'fr-FR',
  'Europe/Madrid': 'es-ES', 'Europe/Rome': 'it-IT',
  'Europe/Amsterdam': 'nl-NL', 'Europe/Warsaw': 'pl-PL',
  'Europe/Lisbon': 'pt-PT', 'America/Sao_Paulo': 'pt-BR',
  'Europe/Stockholm': 'sv-SE', 'Europe/Copenhagen': 'da-DK',
  'Europe/Oslo': 'nb-NO', 'Europe/Helsinki': 'fi-FI',
  'Europe/Prague': 'cs-CZ', 'Asia/Tokyo': 'ja-JP',
  'Asia/Seoul': 'ko-KR', 'Asia/Shanghai': 'zh-CN',
  'Asia/Taipei': 'zh-TW', 'Asia/Kolkata': 'hi-IN',
  'Asia/Riyadh': 'ar-SA',
};

export function deviceDateRegion(timeZone?: string): string | undefined {
  try {
    return DEVICE_REGIONS[timeZone || new Intl.DateTimeFormat().resolvedOptions().timeZone];
  } catch {
    return undefined;
  }
}
