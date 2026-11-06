import {editProfileImage} from './profile-image-editor';
import {showToast} from './toasts';
import {responsiveArtwork} from './responsive-artwork';
import {PROFILE_MOBILE_BACKGROUND_ASPECT} from './profile-crop-geometry';

export function mountGalleryArtworkActions(viewer:HTMLDialogElement,info:any,signal:AbortSignal,
  selected:()=>{url?:string;artwork:boolean},pinned:(url:string|undefined)=>void) {
  if(!info.bannerViewer)return ()=>{};
  const actions=document.createElement('div');actions.className='gallery-artwork-actions';
  const pin=document.createElement('button');pin.type='button';pin.className='gallery-primary-action';pin.textContent='Use for this game';
  const profile=document.createElement('button');profile.type='button';profile.textContent='Use as profile background';
  actions.append(pin,profile);viewer.append(actions);
  let saved:string|undefined;
  const write=async(path:string,body:unknown,method='PATCH')=>{
    const response=await fetch(path,{method,signal,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    if(!response.ok)throw new Error('Unable to save artwork. Please try again.');
  };
  const paint=()=>{
    const current=selected();actions.hidden=!current.artwork||!current.url;
    pin.hidden=info.type!=='game';pin.textContent=saved===current.url?'Use automatic banner':'Use for this game';
  };
  pin.addEventListener('click',async()=>{
    const current=selected();if(!current.url)return;
    pin.disabled=true;
    try{const image=saved===current.url?null:current.url;
      await write(`/api/proxy/tracking/games/works/${info.id}/banner`,{image},'PUT');
      saved=image||undefined;pinned(saved);paint();showToast(image?'Game banner saved.':'Automatic banners restored.','success');
    }catch(error){if(!signal.aborted)showToast((error as Error).message,'error');}finally{pin.disabled=false;}
  },{signal});
  profile.addEventListener('click',async()=>{
    const {url}=selected();if(!url)return;
    // Reuse the full image already displayed by the viewer, never its metadata thumbnail.
    const image=responsiveArtwork(url,{size:'original',route:'direct'})?.src||url;
    await editProfileImage(image,'Profile background',window.innerWidth/(window.innerWidth<=650?220:330),signal,
      async(crop,editorSignal)=>{
        const response=await fetch('/api/proxy/profile/me/background',{method:'PUT',signal:editorSignal,
          headers:{'Content-Type':'application/json'},body:JSON.stringify({image,crop})});
        if(!response.ok)throw new Error('Unable to save background. Please try again.');
        showToast('Profile background saved.','success');
      },{mobileAspect:PROFILE_MOBILE_BACKGROUND_ASPECT});
  },{signal});
  signal.addEventListener('abort',()=>actions.remove(),{once:true});
  return (url?:string)=>{if(url!==undefined)saved=url||undefined;paint();};
}
