export interface LegalConfig {
  operator_name: string; operator_address: string; contact_email: string;
  hosting_details: string; retention_details: string; transfer_details: string;
}
export const emptyLegalConfig: LegalConfig = {operator_name:'', operator_address:'', contact_email:'', hosting_details:'', retention_details:'', transfer_details:''};
export type LegalPublication = LegalConfig & {enabled: boolean};
let cached: Promise<LegalPublication> | undefined;
let expires = 0;

/** Public disclosures only. Share requests across footers; retry failures after five seconds. */
export function legalConfig(): Promise<LegalPublication> {
  if (cached && Date.now() < expires) return cached;
  expires = Date.now() + 5000;
  cached = (async () => {
    const empty = {...emptyLegalConfig, enabled:false};
    try {
      const response = await fetch(`http://127.0.0.1:${process.env.BACKEND_PORT || '7331'}/legal/config`, {signal:AbortSignal.timeout(1000)});
      if (!response.ok) return empty;
      const data = await response.json();
      const config = Object.fromEntries(Object.keys(emptyLegalConfig).map(key => [key, typeof data[key] === 'string' ? data[key] : ''])) as unknown as LegalConfig;
      return data.enabled === true && !legalDraft(config) ? {...config, enabled:true} : empty;
    } catch { return empty; }
  })();
  return cached;
}
export function legalDraft(config: LegalConfig): boolean {
  return Object.keys(emptyLegalConfig).some(key => !config[key as keyof LegalConfig].trim());
}
export function legalHref(pathname: string, page: string): string {
  return `${pathname.startsWith('/dev/') ? '/dev' : ''}/${page}`;
}
