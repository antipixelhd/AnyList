/**
 * Production-browser regression for populated, restored profile tabs.
 * Uses a synthetic backend; never connects to an application database.
 *
 * npm --prefix frontend run build
 * agent-browser --session profile-css open about:blank
 * agent-browser --session profile-css get cdp-url > .venv/profile-css-cdp.txt
 * node scripts/verify_profile_snapshot_styles.mjs --cdp-file .venv/profile-css-cdp.txt
 * Optional: --build-dir frontend/dist --output .venv/profile-css-browser.json
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import {createRequire} from 'node:module';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

const options=new Map();
for(let i=2;i<process.argv.length;i+=2){
  const option=process.argv[i],value=process.argv[i+1];
  if(!['--cdp-file','--build-dir','--output'].includes(option)||!value)throw Error('Expected --cdp-file PATH [--build-dir PATH] [--output PATH]');
  options.set(option,value);
}
if(!options.has('--cdp-file'))throw Error('An isolated browser session and --cdp-file are required.');
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const listen=server=>new Promise(resolve=>server.listen(0,'127.0.0.1',()=>resolve(server.address().port)));
let owner=true;
const rows=Array.from({length:100},(_,i)=>({id:i+1,subject:{catalogue_id:i+1},title:`Fixture title ${String(i+1).padStart(3,'0')}`,type:'movie',status:'completed',progress:1,score:7,genres:[],year:2024,poster:null,updated_at:'2026-11-14T12:00:00Z'}));
const backend=http.createServer((req,res)=>{
  const path=new URL(req.url,'http://fixture.test').pathname;
  const profile={id:3,username:'other',display_name:'Fixture Profile',owner,counts:{movies:100,series:0},favorites:[],recent_activity:[],people:[],combine_lists:true};
  let body={enabled:false,has_users:true,enable_logged_out_navigation:false};
  if(path==='/profile/avatar/4'){
    res.setHeader('Content-Type','image/svg+xml');
    res.end('<svg xmlns="http://www.w3.org/2000/svg" width="1056" height="1056"><rect width="1056" height="1056" fill="#294159"/><text x="528" y="700" text-anchor="middle" font-family="Arial" font-size="560" fill="#badcf5">P</text></svg>');return;
  }
  if(path==='/auth/me')body={id:owner?3:2,username:owner?'other':'viewer',is_admin:false,can_access_games:true};
  if(path==='/profile/me')body={profile_color:'#3db4f2',privacy_level:'private'};
  if(path==='/auth/settings')body={has_effective_tmdb_key:true};
  if(['/auth/connections','/auth/scrobble-connections','/sync/season-overrides','/auth/device/grants','/sync/status','/tracking/notifications','/notifications'].includes(path))body=[];
  if(path==='/imports/active')body={active:null};
  if(path==='/sync/schedule')body={interval:null,next_due_at:null};
  if(path==='/tracking/people/other'||path==='/tracking/people/other/header')body=profile;
  if(path==='/tracking/people/other/social')body={...profile,people:[{id:4,username:'provider',display_name:'Provider',following:true,follower:true,has_avatar:true}]};
  if(path==='/tracking/people/other/favorites')body={profile,screen:[{id:11,type:'movie',title:'Screen favorite',poster:null}],games:{items:[{id:12,type:'game',title:'Game favorite',poster:null}],total:1}};
  if(path.includes('/stats/summary'))body=null;
  if(path.includes('/games/users/other/'))body={results:[],total:0};
  if(path==='/tracking/profile/other/all/list'||path==='/tracking/games/users/other/list')body={profile,results:rows,total:100,offset:0,has_more:false,group_counts:{completed:100},status_counts:{completed:100},genres:[],years:[]};
  if(path.endsWith('/index'))body={complete:false,results:[]};
  if(path==='/tracking/profile/other/stats/overview')body={status:'ready',owner,profile,media_type:'all',section:'overview',overview:{media_type:'all',totals:{listed_titles:0,watched_titles:0,episode_plays:0,distinct_episodes:0,watch_minutes:0,watch_days:0,planned_minutes:0,planned_days:0,mean_score:null,standard_deviation:null,rated_titles:0},scores:[],episode_counts:[],statuses:[],formats:[],countries:[],release_years:[],watch_years:[],coverage:{}}};
  res.setHeader('Content-Type','application/json');res.end(JSON.stringify(body));
});
const servers=[backend];let ws;
try{
  process.env.BACKEND_PORT=String(await listen(backend));
  process.env.ASTRO_NODE_AUTOSTART='disabled';process.env.PREVIEW_SLOT='';
  const buildDir=resolve(options.get('--build-dir')||'frontend/dist');
  const {handler}=await import(pathToFileURL(resolve(buildDir,'server/entry.mjs')));
  const compression=createRequire(new URL('../frontend/package.json',import.meta.url))('compression')();
  const frontend=http.createServer((req,res)=>{
    if(req.url==='/__fixture/reset'){res.setHeader('Content-Type','text/html');res.end('<!doctype html><title>Fixture reset</title>');return;}
    compression(req,res,()=>handler(req,res));
  });
  servers.push(frontend);const port=await listen(frontend),origin=`http://127.0.0.1:${port}`;
  const endpoint=fs.readFileSync(options.get('--cdp-file'),'utf8').trim().replace(/^\uFEFF/,'');
  ws=new WebSocket(endpoint);await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
  let id=0;const pending=new Map();
  ws.onmessage=event=>{const message=JSON.parse(event.data);if(message.id){const request=pending.get(message.id);pending.delete(message.id);message.error?request.reject(Error(JSON.stringify(message.error))):request.resolve(message.result);}};
  const call=(method,params={},sessionId)=>new Promise((resolve,reject)=>{pending.set(++id,{resolve,reject});ws.send(JSON.stringify({id,method,params,sessionId}));});
  const {targetInfos}=await call('Target.getTargets'),target=targetInfos.find(target=>target.type==='page');
  assert(target,'The isolated browser needs an open tab.');
  const {sessionId}=await call('Target.attachToTarget',{targetId:target.targetId,flatten:true});
  const c=(method,params={})=>call(method,params,sessionId);
  const evaluate=async expression=>{
    const result=await c('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
    if(result.exceptionDetails)throw Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const wait=async expression=>{
    const deadline=Date.now()+20000;
    while(Date.now()<deadline){try{if(await evaluate(expression))return;}catch(error){if(!/context|navigat/i.test(error.message))throw error;}await sleep(50);}
    throw Error(`Timed out waiting for ${expression}`);
  };
  const navigate=async(path,reload=false)=>{
    const previous=await evaluate('performance.timeOrigin');
    await c(reload?'Page.reload':'Page.navigate',reload?{}:{url:origin+path});
    await wait(`performance.timeOrigin!==${previous}&&location.pathname===${JSON.stringify(path)}&&document.readyState==='complete'`);
    if(path!=='/__fixture/reset')await wait("!!document.querySelector('[data-profile-panels]')");
  };
  await c('Network.enable');await c('Page.enable');await c('Page.bringToFront');await c('Emulation.setFocusEmulationEnabled',{enabled:true});
  await c('Page.addScriptToEvaluateOnNewDocument',{source:`
    window.__profileStyleCheck={errors:[],requests:[]};
    addEventListener('error',event=>{if(event.message)__profileStyleCheck.errors.push(event.message);});
    addEventListener('unhandledrejection',event=>__profileStyleCheck.errors.push(String(event.reason)));
    const original=fetch;window.fetch=(...args)=>{__profileStyleCheck.requests.push(String(args[0]));return original(...args);};
  `});
  const results=[];
  for(const width of [1440,390])for(const isOwner of [true,false]){
    owner=isOwner;
    await c('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:width<650});
    await c('Emulation.setTouchEmulationEnabled',{enabled:width<650,maxTouchPoints:1});
    await c('Network.setCookie',{name:'token',value:isOwner?'fixture-owner':'fixture-visitor',url:origin,path:'/'});
    await navigate('/__fixture/reset');await evaluate('sessionStorage.clear()');
    if(isOwner)await evaluate(`sessionStorage.setItem('anylist:profile-tabs:v1',JSON.stringify({identity:'3:true',entries:[{key:'/user/other/favorites',title:'Old incomplete snapshot',content:'<div>Incomplete</div>',styles:'',checkedAt:Date.now(),bytes:100}]}))`);
    await navigate('/user/other');
    assert.equal(await evaluate("document.querySelector('.profile-tabs').dataset.profileOwner"),isOwner?'yes':'no');
    if(isOwner){
      assert.equal(await evaluate("sessionStorage.getItem('anylist:profile-tabs:v1')"),null,'Legacy snapshots must be discarded');
      await wait("JSON.parse(sessionStorage.getItem('anylist:profile-tabs:v2')||'null')?.entries.length>=6");
    }
    const checkTabs=async label=>{
      const observations=await evaluate(`(async()=>{
        const results=[],nav=document.querySelector('.profile-tabs');
        for(const key of ['favorites','social','list','games','stats','overview']){
          const path='/user/other'+(key==='overview'?'':'/'+key);
          const start=performance.now();[...nav.querySelectorAll('a')].find(link=>link.pathname===path).click();
          const deadline=performance.now()+15000;while(nav.hasAttribute('aria-busy')&&performance.now()<deadline)await new Promise(resolve=>setTimeout(resolve,10));
          if(nav.hasAttribute('aria-busy')||location.pathname!==path)throw Error('Failed tab '+key);
          await new Promise(requestAnimationFrame);
          const active=[...document.querySelector('[data-profile-panels]').children].find(panel=>!panel.hasAttribute('data-profile-preparing'));
          const card=active.querySelector('.favorite-card'),remove=active.querySelector('.favorite-remove'),social=active.querySelector('.social-person-card'),avatar=social?.querySelector('img');
          if(avatar)await avatar.decode();
          results.push({key,ms:performance.now()-start,cardPosition:card&&getComputedStyle(card).position,removePosition:remove&&getComputedStyle(remove).position,socialWidth:social?.getBoundingClientRect().width,avatarWidth:avatar?.getBoundingClientRect().width,inlineStyles:[...document.head.querySelectorAll('style')].map(style=>style.textContent)});
        }
        return results;
      })()`);
      const favorites=observations.find(tab=>tab.key==='favorites'),social=observations.find(tab=>tab.key==='social');
      assert.equal(favorites.cardPosition,'relative',`${label}: favorite card positioning`);
      if(isOwner)assert.equal(favorites.removePosition,'absolute',`${label}: favorite remove button positioning`);
      assert(social.socialWidth>=70&&social.socialWidth<=150,`${label}: social card width ${social.socialWidth}`);
      assert(Math.abs(social.socialWidth-social.avatarWidth)<1,`${label}: avatar must fit the card`);
      for(const tab of observations)assert.equal(new Set(tab.inlineStyles).size,tab.inlineStyles.length,`${label}: duplicate inline CSS after ${tab.key}`);
      assert.deepEqual(await evaluate('__profileStyleCheck.errors'),[]);
      return {label,tabs:observations.map(({inlineStyles,...tab})=>tab)};
    };
    const cycles=[await checkTabs('fresh')];
    if(isOwner){
      const snapshotInfo=await evaluate("JSON.parse(sessionStorage.getItem('anylist:profile-tabs:v2')).entries.map(entry=>({key:entry.key,bytes:entry.bytes,hasInlineStyles:entry.styles.includes('<style')}))");
      assert(snapshotInfo.every(entry=>entry.hasInlineStyles),'Every saved panel must retain production inline CSS');
      for(const fonts of ['cold','warm']){
        await evaluate("document.querySelector('.profile-tabs a[href=\"/user/other/list\"]').click()");
        await wait("location.pathname.endsWith('/list')&&!document.querySelector('.profile-tabs').hasAttribute('aria-busy')");
        await evaluate('scrollTo(0,700)');await sleep(100);
        assert.equal(await evaluate('scrollY'),700);
        if(fonts==='cold')await c('Network.clearBrowserCache');
        await navigate('/user/other/list',true);
        await wait("JSON.parse(sessionStorage.getItem('anylist:profile-tabs:v2')||'null')?.entries.length>=6");
        await sleep(200);
        assert.equal(await evaluate('scrollY'),700,`${fonts} font reload must preserve scroll`);
        cycles.push(await checkTabs(`restored-${fonts}`));
        assert.equal(await evaluate("__profileStyleCheck.requests.filter(url=>new URL(url,location.href).pathname.startsWith('/user/')).length"),0,'Restored owner tabs reuse snapshots without page refetches');
      }
      await navigate('/user/other/list');await evaluate('scrollTo(0,700)');await sleep(100);
      await navigate('/user/other/social');
      await evaluate('history.back()');await wait("location.pathname.endsWith('/list')&&document.readyState==='complete'&&!!document.querySelector('[data-profile-panels]')");await sleep(250);
      assert.equal(await evaluate('scrollY'),700,'Back navigation preserves the list position');
      await evaluate('history.forward()');await wait("location.pathname.endsWith('/social')&&document.readyState==='complete'&&!!document.querySelector('[data-profile-panels]')");
      cycles.push(await checkTabs('history-return'));
      results.push({width,owner:isOwner,snapshots:snapshotInfo,cycles});
    }else{
      await navigate('/user/other');cycles.push(await checkTabs('repeat-visit'));
      assert.equal(await evaluate("sessionStorage.getItem('anylist:profile-tabs:v2')"),null,'Visitor tabs stay in memory only');
      results.push({width,owner:isOwner,cycles});
    }
    console.log(JSON.stringify({width,owner:isOwner,cycles:cycles.length,passed:true}));
  }
  if(options.has('--output'))fs.writeFileSync(options.get('--output'),JSON.stringify(results,null,2));
  console.log('All populated profile snapshot/style checks passed.');
}finally{
  ws?.close();
  for(const server of servers){server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
}
