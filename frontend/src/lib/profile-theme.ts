import { api } from './api';

export const DEFAULT_PROFILE_COLOR = '#3db4f2';

export function profileColor(value: unknown): string {
  return typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value)
    ? value.toLowerCase()
    : DEFAULT_PROFILE_COLOR;
}

export function themeVariables(color: string): string {
  return `--accent-100: color-mix(in srgb, ${color} 16%, white); --accent-300: color-mix(in srgb, ${color} 60%, white); --accent-400: color-mix(in srgb, ${color} 78%, white); --accent-500: ${color}; --accent-600: color-mix(in srgb, ${color} 82%, black); --accent-700: color-mix(in srgb, ${color} 72%, black); --accent-900: color-mix(in srgb, ${color} 25%, #0b1622)`;
}

export async function viewerProfileTheme(token?: string | null) {
  if (!token) return { profileColor: DEFAULT_PROFILE_COLOR, applySiteWide: false };
  const preferences = await api.profile.get(token).catch(() => null);
  return {
    profileColor: profileColor(preferences?.profile_color),
    applySiteWide: preferences?.apply_site_wide === true,
  };
}
