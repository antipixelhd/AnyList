export function profileListQuery(params:URLSearchParams,statuses:ReadonlyArray<readonly [string,string]>,defaultSort='title') {
  const query=new URLSearchParams();
  for(const key of ['q','genre']){const value=(params.get(key)||'').trim().slice(0,200);if(value)query.set(key,value);}
  const status=params.get('status');if(statuses.some(([value])=>value===status))query.set('status',status!);
  const year=params.get('year');if(year&&/^\d{4}$/.test(year))query.set('year',year);
  const sort=params.get('sort')||defaultSort;
  query.set('sort',['title','score','progress','updated'].includes(sort)?sort:'title');
  if(params.get('favorites')==='true')query.set('favorites','true');
  return query;
}
