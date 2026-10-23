import type {DetailPerson} from './media-detail-data';
export type CastMode='actors'|'characters';
export type Availability='available'|'empty'|'unknown';
export type GameCharacter={id:number;name:string;image:string|null;providers?:string[];
  performers:{id:number;name:string;url?:string|null;language?:string|null}[]};
export type GameCastData={actors:DetailPerson[];characters:DetailPerson[];mode:CastMode;
  availability:Record<CastMode,Availability>};

export function keepCachedGameCredits(data:{people:unknown[];organizations:unknown[];game_characters?:unknown[];
  character_status:{pending?:boolean;error?:string|null}},cached:GameCastData,otherCredits=false) {
  return !!data.character_status.pending&&data.character_status.error!=='identity_conflict'&&
    !data.people.length&&!data.organizations.length&&!data.game_characters?.length&&
    !!(cached.actors.length||cached.characters.length||otherCredits);
}

/** Presentation keys never create character identities or merge by name. */
export function gameCharacterCards(characters:GameCharacter[],actors:DetailPerson[]):DetailPerson[] {
  const result:DetailPerson[]=characters.map(character=>({id:character.id,kind:'character',
    card_key:`character:${character.id}`,name:character.name,image:character.image,
    url:character.performers.length===1?character.performers[0].url||null:null,
    roles:[...new Map(character.performers.map(person=>[person.id,person.name])).values()],characters:[]}));
  const known=new Set(characters.map(character=>character.id));
  for(const actor of actors){
    for(const name of actor.characters){
      if(actor.performances?.some(role=>known.has(role.id)&&role.name===name))continue;
      result.push({id:actor.id,kind:'character',card_key:JSON.stringify(['performance',actor.id,name]),
        name,image:null,url:actor.url,roles:[actor.name],characters:[]});
    }
  }
  return result;
}

export function gameCastData(actors:DetailPerson[],characters:GameCharacter[],
  credits?:{complete?:boolean;pending?:boolean}|null,characterStatus?:{complete?:boolean}|null):GameCastData {
  const cards=gameCharacterCards(characters,actors),closed=credits?.complete===true&&!credits.pending;
  return {actors,characters:cards,mode:actors.length?'actors':cards.length?'characters':'actors',
    availability:{actors:actors.length?'available':closed?'empty':'unknown',
      characters:cards.length?'available':closed&&characterStatus?.complete===true?'empty':'unknown'}};
}
