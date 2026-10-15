export type SearchSection = 'all' | 'actor' | 'staff' | 'studio';
export type ResultSection = SearchSection | 'movie' | 'series';
export type ContributorScope = 'people'|'actor'|'staff'|'studio';
export type SearchRequest = {kind:'movie'|'series'|'contributors';url:string;scope?:ContributorScope};
export function quickSearchRequests(query:string, mobile:boolean, section:SearchSection): SearchRequest[] {
  const media = !mobile || section === 'all';
  const requests:SearchRequest[] = media ? (['movie','series'] as const).map(kind=>({kind,url:`/api/proxy/tracking/catalog?${new URLSearchParams({media_type:kind,q:query})}`})) : [];
  const scopes:ContributorScope[] = mobile ? section === 'all' ? [] : [section] : ['people','studio'];
  for(const scope of scopes)requests.push({kind:'contributors',scope,url:`/api/proxy/tracking/quick-search/contributors?${new URLSearchParams({q:query,kind:scope})}`});
  return requests;
}
export function mergeContributorResults<T>(current:Record<'actor'|'staff'|'studio',T[]>, incoming:Partial<Record<'actor'|'staff'|'studio',T[]>>, scope?:ContributorScope) {
  return {actor:scope==='studio'||scope==='staff'?current.actor:incoming.actor||[],
    staff:scope==='studio'||scope==='actor'?current.staff:incoming.staff||[],
    studio:scope==='people'||scope==='actor'||scope==='staff'?current.studio:incoming.studio||[]};
}
export function visibleQuickSearchSections(mobile:boolean, selected:SearchSection, combined:boolean, counts:Record<ResultSection,number>): ResultSection[] {
  const sections:ResultSection[] = mobile ? [selected] : [...(combined ? ['all' as const] : ['movie' as const,'series' as const]),'actor','staff','studio'];
  return sections.filter(section=>counts[section]>0);
}
