/** Required notices remain reachable without an account or logged-out browsing. */
export function publicLegalPage(pathname: string): boolean {
  return /^\/(?:impressum|privacy|cookies|credits|about)\/?$/.test(pathname);
}
