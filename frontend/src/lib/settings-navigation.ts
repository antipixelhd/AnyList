export const settingsGroups = [
  {
    label: 'Settings',
    links: [
      { href: '/user-settings', label: 'Profile' },
      { href: '/settings', label: 'Account' },
      { href: '/settings/media', label: 'Movies & series' },
      { href: '/settings/lists', label: 'Lists' },
      { href: '/settings/notifications', label: 'Notifications' },
      { href: '/connections?section=imports', label: 'Import data' },
    ],
  },
  {
    label: 'Apps',
    links: [
      { href: '/connections', label: 'Connections' },
      { href: '/connections?section=apps', label: 'Connected apps' },
      { href: '/connections?section=developer', label: 'Developer' },
    ],
  },
];

export function isSettingsLinkActive(href: string, current: URL): boolean {
  const target = new URL(href, current);
  if (target.pathname !== current.pathname) return false;
  if (target.pathname !== '/connections') return true;
  // Unknown connection sections fall back to the Connections view.
  const requested = current.searchParams.get('section');
  const section = ['imports', 'apps', 'developer'].includes(requested ?? '') ? requested : null;
  return target.searchParams.get('section') === section;
}
