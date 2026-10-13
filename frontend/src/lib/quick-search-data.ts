export type SearchSection = 'all' | 'actor' | 'staff' | 'studio';
export type ResultSection = SearchSection | 'movie' | 'series';
export type SearchRequest = {kind:'movie'|'series'|'contributors';url:string};
export function quickSearchRequests(query:string, mobile:boolean, section:SearchSection): SearchRequest[] {
  const media = !mobile || section === 'all';
  const requests:SearchRequest[] = media ? (['movie','series'] as const).map(kind=>({kind,url:`/api/proxy/tracking/catalog?${new URLSearchParams({media_type:kind,q:query})}`})) : [];
  if (!mobile || section !== 'all') requests.push({kind:'contributors',url:`/api/proxy/tracking/quick-search/contributors?${new URLSearchParams({q:query,kind:mobile?section:'all'})}`});
  return requests;
}
export function visibleQuickSearchSections(mobile:boolean, selected:SearchSection, combined:boolean, counts:Record<ResultSection,number>): ResultSection[] {
  const sections:ResultSection[] = mobile ? [selected] : [...(combined ? ['all' as const] : ['movie' as const,'series' as const]),'actor','staff','studio'];
  return sections.filter(section=>counts[section]>0);
}
