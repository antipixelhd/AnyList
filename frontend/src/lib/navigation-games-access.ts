/** HTML text only: inspecting speculative markup must not start image requests. */
export function snapshotHasGames(html:string) {
  return /\bhref\s*=\s*["'](?:https?:\/\/[^/"']+)?\/games\/[1-9]\d*(?:[/?#"'])/i.test(html);
}

/** Mixed snapshots retain game content only after a fresh viewer/approval check. */
export async function authorizeGameSnapshot(viewer:string,signal:AbortSignal,fetcher:typeof fetch=fetch) {
  if(viewer==='anonymous'||signal.aborted)return false;
  try{
    const response=await fetcher('/api/proxy/auth/me',{signal,credentials:'same-origin',cache:'no-store'});
    if(!response.ok||response.redirected)return false;
    const user=await response.json();
    return !signal.aborted&&String(user.id)===viewer&&user.can_access_games===true;
  }catch{return false;}
}
