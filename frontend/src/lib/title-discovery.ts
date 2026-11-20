/** Resolve public metadata without creating a personal list entry. */
export async function resolveTitleId(type:'movie'|'series',tmdbId:number,signal:AbortSignal,fetcher:typeof fetch=fetch) {
  const response=await fetcher(`/api/proxy/tracking/catalog/${type}/${tmdbId}`,{method:'POST',signal});
  const data=await response.json().catch(()=>null);
  if(!response.ok||!Number.isSafeInteger(data?.id)||data.id<=0)
    throw new Error('Could not load the title. Check your metadata key in Settings and try again.');
  return data.id as number;
}
