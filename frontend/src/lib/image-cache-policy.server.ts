// Share only this public flag, never personal data or permissions.
let expires=0,enabled=false,pending:Promise<boolean>|undefined;
export function imageCacheEnabled():Promise<boolean> {
  if(Date.now()<expires)return Promise.resolve(enabled);
  if(pending)return pending;
  pending=fetch(`http://127.0.0.1:${process.env.BACKEND_PORT??'7331'}/profile/public-access-status`,{signal:AbortSignal.timeout(1500)}).then(response=>{
    if(!response.ok)throw new Error('Image cache policy unavailable');
    return response.json();
  }).then(flags=>{
    enabled=flags.image_cache_enabled===true;expires=Date.now()+30_000;return enabled;
  }).catch(()=>{expires=Date.now()+5000;return enabled;}).finally(()=>{pending=undefined;});
  return pending;
}
