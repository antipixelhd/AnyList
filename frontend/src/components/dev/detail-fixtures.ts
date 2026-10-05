export type SampleKey = 'series' | 'movie' | 'game' | 'book';
export type Person = { name: string; role: string; image?: string; url: string };
export type Episode = { title: string; minutes: number; image: string; date: string };
export type Relation = { title: string; relation: 'Previous entry' | 'Next entry' | 'Source' | 'Adaptation' | 'Spin-off' | 'Shared universe' | 'Expansion'; detail: string; image: string; url: string };
export type Suggestion = { title: string; detail: string; image: string; url: string };
export type SteamReview = { label: string; percent: number; count: number; sentiment: 'positive' | 'mixed' | 'negative' };
export const gameReviewSamples: Record<string, [SteamReview, SteamReview]> = {
  positive: [{label: 'Overwhelmingly Positive', percent: 95, count: 21475, sentiment: 'positive'}, {label: 'Overwhelmingly Positive', percent: 96, count: 26743, sentiment: 'positive'}],
  mixed: [{label: 'Very Positive', percent: 92, count: 89, sentiment: 'positive'}, {label: 'Mixed', percent: 55, count: 14215, sentiment: 'mixed'}],
  negative: [{label: 'Mostly Negative', percent: 21, count: 37224, sentiment: 'negative'}, {label: 'Mixed', percent: 55, count: 464094, sentiment: 'mixed'}],
};
export interface DetailSample {
  title: string; kind: string; year: string; subtitle: string; creator: string;
  summary: string; more: string; genres: string[]; poster: string; backdrop: string;
  facts: [string, string][]; score?: string; votes?: string;
  gameReviews?: [SteamReview, SteamReview];
  offer?: {price: number; regularPrice: number; currency: string; store: string; region: string; url: string};
  progress: number; total: number; unit: string; action: string; increment: number;
  next: string; nextMeta: string; note: string; people: Person[];
  gallery: string[]; source: string; sourceUrl: string; officialUrl: string;
  seasons?: Episode[][]; relations: Relation[]; similar?: Suggestion[];
}
export const tmdbImage = (path: string, size = 'w500') => `https://image.tmdb.org/t/p/${size}/${path}`;
const sev = tmdbImage('ixgFmf1X59PUZam2qbAfskx2gQr.jpg', 'w1280');
const dune = tmdbImage('eZ239CUp1d6OryZEBPnO2n87gMG.jpg', 'w1280');
const steam = 'https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/870780/';
const control = `${steam}930bb8e7ff63d96dd456948b38c307d86fc1fad7/page_bg_raw.jpg`;
const cover = 'https://covers.openlibrary.org/b/isbn/9780441172719-L.jpg';
const episode = (title: string, minutes: number, image: string, date: string): Episode => ({title, minutes, image: tmdbImage(image), date});
const seasons = [
  [
    episode('Good News About Hell', 57, 'sP3GZe9j7BY3CAyZGFuZxxnGpN1.jpg', '18 Feb 2022'),
    episode('Half Loop', 53, 'wIQP0P2tyE7VRIN3xR8Efq7UgKt.jpg', '18 Feb 2022'),
    episode('In Perpetuity', 56, 'yezUFOqLt0U3haSYc5gIBzfsPvo.jpg', '25 Feb 2022'),
    episode('The You You Are', 46, 'dzQJeDznS0I4spAqTImiLH9p252.jpg', '4 Mar 2022'),
    episode('The Grim Barbarity of Optics and Design', 43, 'oy4BZotFbNX5YoPkWuWF3kcdMVw.jpg', '11 Mar 2022'),
    episode('Hide and Seek', 40, '3fQp0pUtgDkqqyEbXY8KKCZPMU7.jpg', '18 Mar 2022'),
    episode('Defiant Jazz', 49, 'gHoKlbC2esqq3SXm0ZCcl3bzpFZ.jpg', '25 Mar 2022'),
    episode("What's for Dinner?", 48, '8Gv9ELume0jo7gz7R2h4tPmVLsD.jpg', '1 Apr 2022'),
    episode('The We We Are', 41, 'dzx8D2B0ELncXuCNwtPCags43Q.jpg', '8 Apr 2022'),
  ],
  ['Hello, Ms. Cobel', 'Goodbye, Mrs. Selvig', 'Who Is Alive?', "Woe's Hollow", 'Trojan’s Horse', 'Attila', 'Chikhai Bardo', 'Sweet Vitriol', 'The After Hours', 'Cold Harbor'].map((title, i) => ({title, minutes: 48 + i % 4, image: '', date: '2025'})),
];
export const samples: Record<SampleKey, DetailSample> = {
  series: {
    title: 'Severance', kind: 'Series', year: '2022', subtitle: 'A life at work. A world outside.', creator: 'Created by Dan Erickson',
    summary: 'At Lumon Industries, a team of office workers has undergone a procedure that separates their work memories from their personal lives. Inside, the routine seems ordinary. Outside, the questions begin.',
    more: 'When a mysterious colleague appears beyond the office walls, Mark begins to question the arrangement. The series follows the people on both sides of that divide, and what happens when their carefully separated worlds start to overlap.',
    genres: ['Drama', 'Mystery', 'Sci-Fi & Fantasy'], poster: tmdbImage('pPHpeI2X1qEd1CS1SeyrdhZ4qnT.jpg'), backdrop: sev,
    facts: [['Format', 'TV series'], ['First aired', '18 February 2022'], ['Seasons', '2'], ['Episodes', '19'], ['Network', 'Apple TV'], ['Creator', 'Dan Erickson'], ['Language', 'English']],
    score: '8.7', votes: '6,420', progress: 5, total: 19, unit: 'episodes', action: 'Watching', increment: 1,
    next: 'Hide and Seek', nextMeta: 'S01 · E06 · 40 min', note: 'Come back to the opening scene after finishing the season.',
    people: [
      {name: 'Adam Scott', role: 'Mark Scout', image: tmdbImage('b82C29R6fGiPoqIglQ4lzS6q2YX.jpg', 'w185'), url: 'https://www.themoviedb.org/person/19278-adam-scott'},
      {name: 'Britt Lower', role: 'Helly Riggs', image: tmdbImage('5XIcTMDSyj7hRICQAcnY9U83ujF.jpg', 'w185'), url: 'https://www.themoviedb.org/tv/95396-severance/cast'},
      {name: 'Zach Cherry', role: 'Dylan George', image: tmdbImage('fT3Wv8ef0Vn0daHWAObCp2Bd4Y.jpg', 'w185'), url: 'https://www.themoviedb.org/tv/95396-severance/cast'},
      {name: 'John Turturro', role: 'Irving Bailiff', image: tmdbImage('6O9W9cJW0kCqMzYeLupV9oH0ftn.jpg', 'w185'), url: 'https://www.themoviedb.org/tv/95396-severance/cast'},
    ],
    gallery: [sev, tmdbImage('sP3GZe9j7BY3CAyZGFuZxxnGpN1.jpg', 'w780'), tmdbImage('dzQJeDznS0I4spAqTImiLH9p252.jpg', 'w780')],
    source: 'TMDB', sourceUrl: 'https://www.themoviedb.org/tv/95396-severance', officialUrl: 'https://tv.apple.com/show/severance/umc.cmc.1srk2goyh2q2zdxcx605w8vtx', seasons,
    relations: [],
    similar: [
      {title:'Dark', detail:'2017 · Series', image:tmdbImage('apbrbWs8M9lyOpJYU5WXrpFbk1Z.jpg'), url:'https://www.themoviedb.org/tv/70523-dark'},
      {title:'Silo', detail:'2023 · Series', image:tmdbImage('zBx1X06G1OlndbXTCZI13FECNz2.jpg'), url:'https://www.themoviedb.org/tv/125988-silo'},
      {title:'Mr. Robot', detail:'2015 · Series', image:tmdbImage('kv1nRqgebSsREnd7vdC2pSGjpLo.jpg'), url:'https://www.themoviedb.org/tv/62560-mr-robot'},
    ],
  },
  movie: {
    title: 'Dune: Part Two', kind: 'Movie', year: '2024', subtitle: 'Long live the fighters.', creator: 'Directed by Denis Villeneuve',
    summary: 'On the desert world of Arrakis, Paul Atreides finds a new home among the Fremen. As he and Chani fight for their future, a war for the planet’s most valuable resource becomes a struggle over destiny itself.',
    more: 'The second chapter of Denis Villeneuve’s adaptation follows Paul as he navigates the pull of prophecy, the cost of revenge, and the people who see very different futures in him.',
    genres: ['Science Fiction', 'Adventure'], poster: tmdbImage('6izwz7rsy95ARzTR3poZ8H6c5pp.jpg'), backdrop: dune,
    facts: [['Format', 'Feature film'], ['Released', '1 March 2024'], ['Runtime', '2h 47m'], ['Director', 'Denis Villeneuve'], ['Studio', 'Legendary Pictures'], ['Language', 'English'], ['Collection', 'Dune']],
    score: '8.2', votes: '7,830', progress: 0, total: 1, unit: 'watch', action: 'Planning', increment: 1,
    next: 'A night on Arrakis', nextMeta: '2h 47m · Feature film', note: 'Save this for a night with the good speakers.',
    people: [
      {name: 'Timothée Chalamet', role: 'Paul Atreides', image: tmdbImage('dFxpwRpmzpVfP1zjluH68DeQhyj.jpg', 'w185'), url: 'https://www.themoviedb.org/person/1190668-timothee-chalamet'},
      {name: 'Zendaya', role: 'Chani', image: tmdbImage('1qup8tSt95HLbcy2c2xrx4iJNxv.jpg', 'w185'), url: 'https://www.themoviedb.org/person/505710-zendaya'},
      {name: 'Rebecca Ferguson', role: 'Lady Jessica', image: tmdbImage('ra53cM1aNmdH0aFhj8yBqPOj2fb.jpg', 'w185'), url: 'https://www.themoviedb.org/person/93326-rebecca-ferguson'},
      {name: 'Javier Bardem', role: 'Stilgar', image: tmdbImage('zfRID0jx8DKBluPGU9xtk9sZWUt.jpg', 'w185'), url: 'https://www.themoviedb.org/person/3810-javier-bardem'},
    ],
    gallery: [tmdbImage('24Ov8wnusgnzXwjV1eDm0Lzo5da.jpg','w780'), tmdbImage('rRBD8ORo9y34tYkAQJVbn4Ml6tu.jpg','w780')], source: 'TMDB', sourceUrl: 'https://www.themoviedb.org/movie/693134-dune-part-two', officialUrl: 'https://www.dunemovie.com/',
    relations: [{title: 'Dune', relation:'Previous entry', detail: 'Movie · 2021', image: tmdbImage('d5NXSklXo0qyIYkgV94XAgMIckC.jpg'), url: 'https://www.themoviedb.org/movie/438631-dune'}, {title: 'Dune', relation:'Source', detail: 'Book · 1965', image: cover, url: '/dev/details?media=book'}],
    similar: [
      {title:'Arrival', detail:'2016 · Movie', image:tmdbImage('x2FJsf1ElAgr63Y3PNPtJrcmpoe.jpg'), url:'https://www.themoviedb.org/movie/329865-arrival'},
      {title:'Blade Runner 2049', detail:'2017 · Movie', image:tmdbImage('gajva2L0rPYkEWjzgFlBXCAVBE5.jpg'), url:'https://www.themoviedb.org/movie/335984-blade-runner-2049'},
      {title:'Interstellar', detail:'2014 · Movie', image:tmdbImage('yQvGrMoipbRoddT0ZR8tPoR7NfX.jpg'), url:'https://www.themoviedb.org/movie/157336-interstellar'},
    ],
  },
  game: {
    gameReviews: gameReviewSamples.positive,
    offer: {price: 7.99, regularPrice: 39.99, currency: 'EUR', store: 'Steam', region: 'Germany', url: 'https://store.steampowered.com/app/870780/Control_Ultimate_Edition/'},
    title: 'Control', kind: 'Game', year: '2019', subtitle: 'A world beyond explanation.', creator: 'Developed by Remedy Entertainment',
    summary: 'A secret government agency has been taken over by something it cannot explain. As Jesse Faden, you enter the shifting halls of the Oldest House, wield supernatural abilities, and search for the truth behind your past.',
    more: 'Explore a building that refuses to follow the rules, uncover strange objects, and piece together the stories left in its case files. The Ultimate Edition includes The Foundation and AWE expansions.',
    genres: ['Action', 'Adventure', 'Single-player'], poster: `${steam}library_600x900.jpg`, backdrop: control,
    facts: [['Format', 'Action adventure'], ['Released', '27 August 2019'], ['Developer', 'Remedy Entertainment'], ['Publisher', '505 Games'], ['Platforms', 'PC, PlayStation, Xbox'], ['Edition', 'Ultimate Edition'], ['Mode', 'Single-player']],
    progress: 12.5, total: 30, unit: 'hours', action: 'Playing', increment: .5,
    next: 'Back to the Oldest House', nextMeta: '12.5 hours played · PC', note: 'Explore the side rooms before moving on. There’s a lot hiding in the case files.',
    people: [{name: 'Remedy', role: 'Developer', url: 'https://www.remedygames.com/games/control'}, {name: '505 Games', role: 'Publisher', url: 'https://controlgame.com/'}, {name: 'Sam Lake', role: 'Story', url: 'https://www.remedygames.com/games/control'}],
    gallery: [`${steam}ss_8376498631b089e52fb5c75ffe119e0de5e6aed1.1920x1080.jpg`, `${steam}ss_5a16ce565951479e142c56a23f19d88333d84945.1920x1080.jpg`], source: 'Steam / publisher', sourceUrl: 'https://store.steampowered.com/app/870780/Control_Ultimate_Edition/', officialUrl: 'https://controlgame.com/',
    relations: [{title:'Alan Wake', relation:'Shared universe', detail:'Game · 2010', image:'https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/108710/library_600x900.jpg', url:'https://store.steampowered.com/app/108710/Alan_Wake/'}],
  },
  book: {
    title: 'Dune', kind: 'Book', year: '1965', subtitle: 'Beyond fear, a universe awaits.', creator: 'Written by Frank Herbert',
    summary: 'On Arrakis, water is scarce and spice is everything. When House Atreides takes control of the desert planet, young Paul is drawn into a world of ancient customs, political betrayal, and a future that only he can see.',
    more: 'Frank Herbert’s novel brings together ecology, religion, and the consequences of power in a story that begins with one family and reaches far beyond a single world.',
    genres: ['Science fiction', 'Fiction', 'Space opera'], poster: cover, backdrop: dune,
    facts: [['Format', 'Novel'], ['First published', '1965'], ['Author', 'Frank Herbert'], ['Series', 'Dune · Book 1'], ['Edition', 'Ace · Paperback'], ['Pages', '896'], ['Language', 'English'], ['ISBN', '9780441172719']],
    progress: 214, total: 896, unit: 'pages', action: 'Reading', increment: 20,
    next: 'A little further into the desert', nextMeta: 'Page 214 of 896 · Paperback', note: 'The ecology of Arrakis is just as interesting as the politics.',
    people: [{name: 'Frank Herbert', role: 'Author', url: 'https://openlibrary.org/authors/OL79034A/Frank_Herbert'}, {name: 'Ace Books', role: 'This edition', url: 'https://www.penguinrandomhouse.com/books/352036/dune-by-frank-herbert/'}],
    gallery: [cover, dune], source: 'Open Library', sourceUrl: 'https://openlibrary.org/isbn/9780441172719', officialUrl: 'https://www.penguinrandomhouse.com/books/352036/dune-by-frank-herbert/',
    relations: [{title:'Dune Messiah', relation:'Next entry', detail:'Book · 1969', image:'https://covers.openlibrary.org/b/isbn/9780593201732-L.jpg', url:'https://openlibrary.org/isbn/9780593201732'}, {title:'Dune', relation:'Adaptation', detail:'Movie · 2021', image:tmdbImage('d5NXSklXo0qyIYkgV94XAgMIckC.jpg'), url:'https://www.themoviedb.org/movie/438631-dune'}, {title:'Dune: Part Two', relation:'Adaptation', detail:'Movie · 2024', image:tmdbImage('6izwz7rsy95ARzTR3poZ8H6c5pp.jpg'), url:'/dev/details?media=movie'}],
  },
};
