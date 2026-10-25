import type {DetailPerson,GameCreditPerformance} from './media-detail-data';

export const UNKNOWN_GAME_LANGUAGE='unknown';
const names=new Intl.DisplayNames(['en'],{type:'language'});
function code(value:string) {
  if(!/^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$/i.test(value))return UNKNOWN_GAME_LANGUAGE;
  try{return new Intl.Locale(value).toString();}catch{return UNKNOWN_GAME_LANGUAGE;}
}
function languages(performance:GameCreditPerformance) {
  return [...new Set(performance.languages.length?performance.languages.map(code):[UNKNOWN_GAME_LANGUAGE])];
}
function performances(person:DetailPerson):GameCreditPerformance[] {
  if(person.credit_performances?.length)return person.credit_performances;
  // Compatibility with older cached projections. Several aggregate languages
  // establish person membership, but cannot assign particular characters.
  const values=person.languages||[];
  return [{languages:values,characters:values.length>1?[]:person.characters}];
}
export function gameCastLanguages(actors:DetailPerson[]):[string,string][] {
  const found=new Set(actors.flatMap(actor=>performances(actor).flatMap(languages)));
  const label=(value:string)=>value===UNKNOWN_GAME_LANGUAGE?'Language unknown':names.of(value)||value;
  const sorted=[...found].sort((a,b)=>a===b?0:a==='en'?-1:b==='en'?1:a===UNKNOWN_GAME_LANGUAGE?1:b===UNKNOWN_GAME_LANGUAGE?-1:label(a).localeCompare(label(b)));
  return [['all','All languages'],...sorted.map(value=>[value,label(value)] as [string,string])];
}
export function gameCastLanguage(actors:DetailPerson[],requested:string) {
  const options=gameCastLanguages(actors);
  return options.length>2&&options.some(([value])=>value===requested)?requested:'all';
}
/** A view only: retained source records and character identities never change. */
export function gameCastRows(actors:DetailPerson[],language='all'):DetailPerson[] {
  const rows=language==='all'?[...actors]:actors.flatMap(actor=>{
    const matched=performances(actor).filter(performance=>languages(performance).includes(language));
    if(!matched.length)return [];
    return [{...actor,characters:[...new Set(matched.flatMap(performance=>performance.characters))],
      credit_performances:matched,languages:language===UNKNOWN_GAME_LANGUAGE?[]:[language],
      performances:actor.performances?.filter(performance=>code(performance.language||'')===language)}];
  });
  const priority=new Map(rows.map(actor=>[actor,Number(performances(actor).some(performance=>languages(performance).some(value=>value==='en'||value.startsWith('en-'))))]));
  return rows.sort((a,b)=>priority.get(b)!-priority.get(a)!);
}
