import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import {artworkIdentity} from '../src/lib/responsive-artwork.ts';
import {preloadDetailGallery} from '../src/lib/detail-gallery-preload.ts';
const source=readFileSync(new URL('../src/lib/media-detail-images.ts',import.meta.url),'utf8');
const context={imageKey:artworkIdentity};
runInNewContext(stripTypeScriptTypes(source.slice(source.indexOf('export function uniqueDetailImages'),source.indexOf('export function mountDetailImages')).replace('export ',''))+';globalThis.unique=uniqueDetailImages;',context);
test('Games retain complete canonical galleries while other consumers retain their display budget',()=>{
  const rows=Array.from({length:23},(_,i)=>`https://images.igdb.com/igdb/image/upload/t_thumb/sc${i}.jpg`);
  assert.equal(context.unique(rows).length,10);
  assert.deepEqual(Array.from(context.unique([...rows,rows[0].replace('t_thumb','t_1080p'),'javascript:alert(1)'],Infinity)),rows);
});

test('opening the shared viewer prepares the selected image first and every remaining slide',async()=>{
  const prepared=[];
  const rows=Array.from({length:22},(_,index)=>`image-${index}`);
  const scope={galleries:{artwork:rows},active:'artwork',current:0,name:'Game',galleryLimit:10,
    viewerPending:()=>false,resetZoom:()=>{},revealPreviews:()=>{},announcement:null,
    carousel:()=>({image:{},skeleton:{}}),fitViewer:()=>{},viewerGroup:()=> 'artwork',
    arrows:[],paintSlots:()=>{},artworkActions:()=>{},imageKey:value=>value,prepareImage:index=>{prepared.push(index);},
    preloadDetailGallery,signal:{aborted:false},lightbox:{open:true}};
  const start=source.indexOf('  const showImage=');
  const end=source.indexOf('  const prepareImage=',start);
  runInNewContext(stripTypeScriptTypes(source.slice(start,end))+';showImage(7);',scope);
  assert.deepEqual(prepared,[7]);
  await Promise.resolve();
  assert.equal(prepared[0],7);
  assert.equal(new Set(prepared).size,22);
  assert.equal(prepared.length,22);
});

test('decoded game artwork retains square and portrait entries instead of dropping them after downloading',async()=>{
  for(const type of ['game','movie'])for(const width of [600,800]){
    const rows=['https://images.example/artwork.jpg'];
    const image={naturalWidth:width,naturalHeight:800,complete:true,fetchPriority:'low'};
    const skeleton={classList:{add(){}},textContent:''};
    const scope={info:{type},galleries:{artwork:rows},active:'artwork',current:0,name:'Entry',standalone:undefined,
      lightbox:{open:true},signal:{aborted:false},root:{querySelectorAll:()=>[]},slots:{querySelector:()=>null},carousel:{slide:()=>({image,skeleton})},
      preparedImages:new WeakMap(),imageKey:value=>value,responsiveArtwork:src=>({src}),
      loadViewerImage:()=>Promise.resolve(true),fitViewer:()=>{},viewerGroup:()=> 'artwork',
      refreshPreviewLoading:()=>{},showImage:()=>{},reveal:()=>{},showToast:()=>{}};
    const start=source.indexOf('  const prepareImage=');
    const end=source.indexOf('  const watchLandscape=',start);
    runInNewContext(stripTypeScriptTypes(source.slice(start,end))+';globalThis.ready=prepareImage(0);',scope);
    await scope.ready;
    assert.equal(scope.galleries.artwork.length,type==='game'?1:0,`${type} ${width}x800`);
  }
});

test('a pending preview keeps its canonical identity before currentSrc becomes available',async()=>{
  const url='https://images.igdb.com/igdb/image/upload/t_1080p/art.jpg';
  const thumbnail={currentSrc:'',src:url.replace('t_1080p','t_cover_small_2x'),dataset:{},getAttribute:()=> 'thumbnail'};
  const image={naturalWidth:0,naturalHeight:0,complete:false,fetchPriority:'low'};
  const skeleton={classList:{add(){}},textContent:''};let previews;
  const scope={info:{type:'game'},galleries:{artwork:[url]},active:'artwork',current:0,name:'Entry',standalone:undefined,
    lightbox:{open:true},signal:{aborted:false},root:{querySelectorAll:()=>[thumbnail]},
    slots:{querySelector:()=>({querySelector:()=>thumbnail})},loadThumbnail:()=>{},carousel:{slide:()=>({image,skeleton})},
    preparedImages:new WeakMap(),imageKey:artworkIdentity,responsiveArtwork:src=>({src}),
    loadViewerImage:(_image,_skeleton,_url,_alt,_priority,preview)=>{previews=preview;return Promise.resolve(false);}};
  const start=source.indexOf('  const prepareImage='),end=source.indexOf('  const watchLandscape=',start);
  runInNewContext(stripTypeScriptTypes(source.slice(start,end))+';globalThis.ready=prepareImage(0);',scope);
  await scope.ready;assert.equal(previews.length,1);assert.equal(previews[0],thumbnail);
});

test('a refreshed canonical game gallery removes stale logos instead of merging them back',()=>{
  let publish;
  const info={type:'game',id:194},galleries={posters:[],artwork:['scene','logo']};
  const scope={info,galleries,signal:{aborted:false},artworkLimit:Infinity,previewInfo:{image:'logo'},
    uniqueDetailImages:rows=>rows,applyImages:data=>{galleries.artwork=data.artwork;},
    root:{addEventListener:(_name,callback)=>{publish=callback;},querySelector:()=>null}};
  const start=source.indexOf("root.addEventListener('anylist:game-primary'");
  const end=source.indexOf('  const resource=',start);
  runInNewContext(stripTypeScriptTypes(source.slice(start,end)),scope);
  publish({detail:{id:194,type:'game',artwork:['scene'],artwork_preview:{image:'scene'}}});
  assert.deepEqual(galleries.artwork,['scene']);
  publish({detail:{id:194,type:'game',artwork:[]}});
  assert.deepEqual(galleries.artwork,[]);
  assert.equal(scope.previewInfo,undefined);
});
