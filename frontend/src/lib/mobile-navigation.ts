export type MobileNavigationItem = {
  label: string;
  icon: string;
  href?: string;
  action?: 'search' | 'logout' | 'theme' | 'notes';
  group?: string;
  active?: boolean;
  hidden?: boolean;
  id?: string;
  ariaLabel?: string;
  notifications?: boolean;
  badge?: number;
};
export type MobileNavigationGroup = {id: string; label: string; items: MobileNavigationItem[]};

export function appMobileNavigation({profile, pathname, signedIn, combineLists,showGames=true,showMoviesSeries=true}: {
  profile: string; pathname: string; signedIn: boolean; combineLists: boolean;showGames?:boolean;showMoviesSeries?:boolean;
}) {
  const items: MobileNavigationItem[] = [
    {label: 'Home', icon: 'home', href: '/home', active: pathname === '/home'},
    ...(signedIn ? [
      {id: 'combined-list', label: 'Lists', icon: 'list', href: `${profile}/list`, active: pathname===`${profile}/list`, hidden: !showMoviesSeries||!combineLists||showGames},
      {id: 'games-list', label: 'Games', icon: 'list', href: `${profile}/games`, active: pathname===`${profile}/games`, hidden: !showGames||showMoviesSeries},
      {id: 'split-lists', label: 'Lists', icon: 'list', group: 'lists', active: ['list','movies','series','games'].some(section=>pathname===`${profile}/${section}`), hidden: !showMoviesSeries||combineLists&&!showGames},
    ] : []),
    {label: 'Browse', icon: 'browse', href: '/browse', active: pathname === '/browse'},
    {label: 'Search', icon: 'search', ...(signedIn ? {action: 'search' as const} : {href: '/search'})},
    ...(signedIn ? [
      {label: 'Profile', icon: 'user', href: profile, active: pathname === profile || pathname === `${profile}/`},
      {label: 'Notifications', icon: 'bell', href: '/recent-events', notifications: true, active: pathname === '/recent-events'},
      {label: 'Settings', icon: 'gear', href: '/user-settings', active: pathname.startsWith('/settings/') || ['/settings','/user-settings','/connections','/admin'].includes(pathname)},
      {label: 'Sign out', icon: 'sign-out', action: 'logout' as const},
    ] : [
      {label: 'Sign up', icon: 'plus', href: '/register'},
      {label: 'Sign in', icon: 'user', href: '/login'},
    ]),
  ];
  const groups: MobileNavigationGroup[] = signedIn ? [{id: 'lists', label: 'Your lists', items: [
    {id:'screen-combined',label:'Movies & Series',icon:'film',href:`${profile}/list`,active:pathname===`${profile}/list`,hidden:!showMoviesSeries||!combineLists},
    {id:'screen-movies',label: 'Movies', icon: 'film', href: `${profile}/movies`, active: pathname===`${profile}/movies`,hidden:!showMoviesSeries||combineLists},
    {id:'screen-series',label: 'Series', icon: 'play', href: `${profile}/series`, active: pathname===`${profile}/series`,hidden:!showMoviesSeries||combineLists},
    {id:'games-sublist',label:'Games',icon:'list',href:`${profile}/games`,active:pathname===`${profile}/games`,hidden:!showGames},
  ]}] : [];
  return {items, groups};
}
