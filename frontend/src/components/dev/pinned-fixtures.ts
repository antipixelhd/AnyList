import {samples,tmdbImage,type DetailSample} from './detail-fixtures';

export const tenet:DetailSample={...samples.movie,title:'Tenet',year:'2020',subtitle:'Time runs out.',creator:'Directed by Christopher Nolan',
  summary:'A secret agent enters a world of international espionage where the direction of time becomes part of the mission.',
  more:'Moving between events in forward and reverse, the Protagonist and Neil work to prevent a catastrophe that threatens the future.',
  genres:['Science Fiction','Action','Thriller'],poster:tmdbImage('rrKH3VhHssGTCfPuaEQkxvEoElv.jpg'),backdrop:tmdbImage('wzJRB4MKi3yK138bJyuL9nx47y6.jpg','w1280'),
  facts:[['Format','Feature film'],['Released','2020'],['Runtime','2h 30m'],['Director','Christopher Nolan'],['Studio','Warner Bros. Pictures'],['Language','English'],['Composer','Ludwig Göransson'],['Cinematography','Hoyte van Hoytema']],
  score:'7.2',votes:'Preview',next:'Tenet',nextMeta:'2h 30m · Movie',note:'Watch the opening sequence again.',
  people:[{name:'John David Washington',role:'The Protagonist',url:'https://www.themoviedb.org/person/1117313'},{name:'Robert Pattinson',role:'Neil',url:'https://www.themoviedb.org/person/11288'},{name:'Elizabeth Debicki',role:'Kat',url:'https://www.themoviedb.org/person/1138091'},{name:'Kenneth Branagh',role:'Andrei Sator',url:'https://www.themoviedb.org/person/11181'}],
  gallery:[tmdbImage('k68nPLbIST6NP96JmTxmZijEvCA.jpg','w1280')],relations:[],similar:[
    {title:'Inception',detail:'2010 · Movie',image:tmdbImage('oYuLEt3zVCKq57qu2F8dT7NIa6f.jpg'),url:'https://www.themoviedb.org/movie/27205'},
    {title:'Interstellar',detail:'2014 · Movie',image:tmdbImage('gEU2QniE6E77NI6lCU6MxlNBvIx.jpg'),url:'https://www.themoviedb.org/movie/157336'},
    {title:'Dunkirk',detail:'2017 · Movie',image:tmdbImage('ebSnODDg9lbsMIaWg2uAbjn7TO5.jpg'),url:'https://www.themoviedb.org/movie/374720'},
  ],
  sourceUrl:'https://www.themoviedb.org/movie/577922',officialUrl:'https://www.tenetfilm.com/'};

export const hamillWorks=[
  ['The Life of Chuck','2025','Albie Krantz'],['The Wild Robot','2024','Thorn (voice)'],['The Boy and the Heron','2023','Granduncle (English voice)'],
  ['The Fall of the House of Usher','2023','Arthur Pym'],['The Sandman','2022','Mervyn Pumpkinhead (voice)'],['Masters of the Universe: Revelation','2021','Skeletor (voice)'],
  ['The Mandalorian','2020','Luke Skywalker'],['Star Wars: The Rise of Skywalker','2019','Luke Skywalker'],['Child’s Play','2019','Chucky (voice)'],
  ['Star Wars: The Last Jedi','2017','Luke Skywalker'],['Star Wars: The Force Awakens','2015','Luke Skywalker'],['Batman: The Animated Series','1992','The Joker (voice)'],
  ['Return of the Jedi','1983','Luke Skywalker'],['The Empire Strikes Back','1980','Luke Skywalker'],['Star Wars','1977','Luke Skywalker'],
];
export const hamillPosters:Record<string,string>={
  'Star Wars':'6FfCtAuVAW8XJjZ7eWeLibRLWTw.jpg',
  'The Empire Strikes Back':'nNAeTmF4CtdSgMDplXTDPOpYzsX.jpg',
  'Return of the Jedi':'jQYlydvHm3kUix1f8prMucrplhm.jpg',
  'Star Wars: The Force Awakens':'wqnLdwVXoBjKibFRR5U3y0aDUhs.jpg',
  'Star Wars: The Last Jedi':'kOVEVeg59E0wsnXmF9nrh6OmWII.jpg',
  'Star Wars: The Rise of Skywalker':'db32LaOibwEliAmSL2jjDF6oDdj.jpg',
};
