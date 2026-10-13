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
