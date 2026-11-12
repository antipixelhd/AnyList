export type RequirementRow={label:string|null;value:string};
export type GameAgeRating={board:string;region:string;rating:string;descriptors:string[];source:string};
export interface GameInfo {
  steam_app_id:string|null;age_ratings:GameAgeRating[];
  requirements?:{minimum:RequirementRow[];recommended:RequirementRow[];platform:'PC';source:'steam'}|null;
  fetched_at?:string;
}
const europe=new Set('AT BE BG HR CY CZ DK EE FI FR GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE GB IS LI NO CH'.split(' '));
export function gameAgeRating(info:GameInfo|null|undefined,country:string) {
  const rows=(info?.age_ratings||[]).filter(row=>typeof row.board==='string'&&typeof row.rating==='string'&&row.rating.trim()&&typeof row.region==='string');
  const preferred=country==='DE'?'USK':['US','CA'].includes(country)?'ESRB':country==='JP'?'CERO':country==='AU'?'ACB':country==='KR'?'GRAC':country==='BR'?'CLASS_IND':europe.has(country)?'PEGI':null;
  const rating=rows.find(row=>row.board===preferred)||rows[0];
  return rating?{label:`${rating.board} ${rating.rating}`,detail:[rating.region,...(rating.descriptors||[]),rating.source==='igdb'?'IGDB':'Steam'].join(' · ')}:null;
}
export function requirementRows(info:GameInfo|null|undefined,part:'minimum'|'recommended'):RequirementRow[] {
  if(info?.requirements?.platform!=='PC')return [];
  return (info.requirements[part]||[]).filter(row=>row&&typeof row.value==='string'&&row.value.trim()&&(!row.label||typeof row.label==='string'));
}
