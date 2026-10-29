/** HTML text only: inspecting speculative markup must not start image requests. */
export function snapshotHasGames(html:string) {
  return /\bhref\s*=\s*["'](?:https?:\/\/[^/"']+)?\/games\/[1-9]\d*(?:[/?#"'])/i.test(html);
}

/** Mixed snapshots retain game content only after a fresh viewer/approval check. */
export async function authorizeGameSnapshot(viewer:string,signal:AbortSignal,fetcher:typeof fetch=fetch,url?:URL) {
  if(viewer==='anonymous'||signal.aborted)return false;
  try{
    const options:RequestInit={signal,credentials:'same-origin',cache:'no-store'};
    const profile=url?.pathname.match(/^\/user\/([^/]+)\/(?:games|stats\/games|favorites)\/?$/);
    const [response,visibility]=await Promise.all([
      fetcher('/api/proxy/auth/me',options),
      profile?fetcher(`/api/proxy/tracking/people/${encodeURIComponent(decodeURIComponent(profile[1]))}/header`,options):Promise.resolve(null),
    ]);
    if(visibility&&(!visibility.ok||visibility.redirected))return false;
    if(!response.ok||response.redirected)return false;
    const user=await response.json();
    return !signal.aborted&&String(user.id)===viewer&&user.can_access_games===true;
  }catch{return false;}
}
