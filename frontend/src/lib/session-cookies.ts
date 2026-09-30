// Cookies are scoped to hosts, not ports. Keep parallel preview sessions separate.
export function sessionCookieName(name: string, slot = process.env.PREVIEW_SLOT): string {
  if (!slot) return name;
  if (!/^(beta|development-[1-9][0-9]*)$/.test(slot)) throw new Error('Invalid PREVIEW_SLOT');
  return `anylist_${slot.replaceAll('-', '_')}_${name}`;
}

export function secureSessionCookie(production: boolean, serverUrl = process.env.SERVER_URL): boolean {
  return production || !!serverUrl?.startsWith('https://');
}
