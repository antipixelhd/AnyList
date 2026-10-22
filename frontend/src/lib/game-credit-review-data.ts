export function reviewPerson(value:string|null|undefined){return value&&/^nm[0-9]{1,28}$/.test(value)?value:null;}
export function reviewTitle(value:string|null|undefined){return value&&/^tt[0-9]{1,28}$/.test(value)?value:null;}
export function reviewKind(value:string|null|undefined){return value==='staff'?'staff':'actor';}
export function reviewHome(native:string,kind:string='actor'){
  const person=reviewPerson(native);return person?`/admin/game-credits/people/${person}?kind=${reviewKind(kind)}`:null;
}
export function reviewMatch(work:number,title:string,native:string,kind:string='actor'){
  if(!Number.isSafeInteger(work)||work<=0||!reviewTitle(title)||!reviewPerson(native))return null;
  return `/admin/game-credits/${work}?${new URLSearchParams({imdb:title,q:title,return_person:native,return_kind:reviewKind(kind)})}`;
}
export interface GameReviewTitle {
  imdb_id:string;title:string;year:number|null;roles:string[];work_id:number|null;game_title:string|null;
  igdb_id:string|null;review_work_id:number|null;status:'bound'|'review_required'|'unmapped';
}
