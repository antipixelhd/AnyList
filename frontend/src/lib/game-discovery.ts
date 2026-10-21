export type GameSearchResult={id:number|null;igdb_id:number;type:'game';title:string;poster?:string|null;year?:string|null;game_class:string};

export function gameResultHref(result:GameSearchResult){
  return result.id?`/games/${result.id}`:`/discover-game?id=${result.igdb_id}`;
}

/** Metadata resolution never creates a personal entry or seeds partial state. */
export async function resolveGameId(igdbId:number,signal:AbortSignal,fetcher:typeof fetch=fetch){
  const response=await fetcher(`/api/proxy/tracking/games/resolve/${igdbId}`,{method:'POST',signal});
  const data=await response.json().catch(()=>null);
  if(!response.ok||!Number.isSafeInteger(data?.id)||data.id<=0)
    throw new Error(typeof data?.detail==='string'?data.detail:'Could not load game details. Please try again.');
  return data.id as number;
}
