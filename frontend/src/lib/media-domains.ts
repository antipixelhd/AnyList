/** Viewer presentation policy. This never grants or revokes route access. */
export type MediaDomains={show_movies_series:boolean;show_games:boolean;show_game_achievements:boolean;combine_lists:boolean;default_sort:string;remove_specials_seasons?:boolean;minimum_popularity?:number;include_rereleases?:boolean;can_access_games?:boolean};
export const defaultMediaDomains:MediaDomains={show_movies_series:true,show_games:true,show_game_achievements:true,combine_lists:true,default_sort:'title'};
export function mediaDomains(value:Partial<MediaDomains>|null|undefined):MediaDomains {
  return {...defaultMediaDomains,...value,show_movies_series:value?.show_movies_series!==false,show_games:value?.show_games!==false,show_game_achievements:value?.show_game_achievements!==false,combine_lists:value?.combine_lists!==false};
}
export function profileMediaDomains(owner:boolean,viewer:MediaDomains):MediaDomains {
  return owner?viewer:{...viewer,show_movies_series:true,show_games:viewer.can_access_games!==false};
}
export function domainListLinks(base:string,preferences:MediaDomains){
  return [
    ...(preferences.show_movies_series?(preferences.combine_lists?[['list','Movie/Series List']]:[['movies','Movie List'],['series','Series List']]):[]),
    ...(preferences.show_games?[['games','Games List']]:[]),
  ].map(([section,label])=>({href:`${base}/${section}`,label}));
}
