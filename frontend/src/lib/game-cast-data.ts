import type {DetailPerson} from './media-detail-data';
import {gameCastRows} from './game-credit-language.ts';
export type GameCharacter={id:number;name:string;image:string|null;providers?:string[];
  performers:{id:number;name:string;url?:string|null;language?:string|null}[]};
export type GameCastData={actors:DetailPerson[];availability:'available'|'empty'|'unknown'};

export function keepCachedGameCredits(data:{people:unknown[];organizations:unknown[];
  character_status:{pending?:boolean;error?:string|null}},cached:GameCastData,otherCredits=false) {
  return !!data.character_status.pending&&data.character_status.error!=='identity_conflict'&&
    !data.people.length&&!data.organizations.length&&
    !!(cached.actors.length||otherCredits);
}

export function gameCastData(actors:DetailPerson[],credits?:{complete?:boolean;pending?:boolean}|null):GameCastData {
  const closed=credits?.complete===true&&!credits.pending;
  return {actors:gameCastRows(actors),availability:actors.length?'available':closed?'empty':'unknown'};
}
