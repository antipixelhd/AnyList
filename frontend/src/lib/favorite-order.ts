export const favoriteKey=(type:string|undefined,id:string|undefined)=>`${type==='game'?'game':'media'}:${id}`;

export function favoriteOrderSaved(saved:string[],current:string[],path:string){
  const prefix=path.includes('/games/')?'game:':'media:';
  const committed=current.filter(key=>key.startsWith(prefix));let index=0;
  return saved.map(key=>key.startsWith(prefix)?committed[index++]:key);
}

/** The two stores can share numeric IDs; persist only domains that changed. */
export function favoriteOrderChanges(current:string[],saved:string[]){
  return ['media','game'].flatMap(domain=>{
    const ids=(keys:string[])=>keys.filter(key=>key.startsWith(domain+':')).map(key=>Number(key.split(':')[1]));
    const order=ids(current);
    return order.join(',')===ids(saved).join(',')?[]:[{
      path:domain==='game'?'/api/proxy/tracking/games/favorites/order':'/api/proxy/tracking/favorites/order',
      body:domain==='game'?{work_ids:order}:{media_ids:order},
    }];
  });
}
