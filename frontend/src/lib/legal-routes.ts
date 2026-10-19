/** Publication is checked separately; enabled notices do not require an account. */
export function publicLegalPage(pathname: string): boolean {
  return /^\/(?:legal-notice|impressum|privacy|cookies|credits|about)\/?$/.test(pathname);
}
