import {closeDialog} from './ui-motion';
import {showToast} from './toasts';

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
    const editor=document.createElement('dialog');editor.className='profile-artwork-editor';editor.setAttribute('aria-label','Profile background');
    const heading=document.createElement('h2');heading.textContent='Profile background';
    const frame=document.createElement('div');frame.className='profile-artwork-canvas';
    const aspect=window.innerWidth/(window.innerWidth<=650?220:330);
    const image=document.createElement('img');image.src=url;image.alt='Profile background crop preview';frame.append(image);
    let values={x:50,y:50,zoom:1};
    const lifetime=new AbortController();
    const cleanup=()=>{lifetime.abort();editor.remove();};
    signal.addEventListener('abort',cleanup,{once:true});
    const status=document.createElement('p');status.className='profile-crop-status';status.textContent='Loading image…';
    const buttons=document.createElement('div');buttons.className='profile-artwork-buttons';
    const cancel=document.createElement('button');cancel.type='button';cancel.textContent='Cancel';
    const save=document.createElement('button');save.type='button';save.className='gallery-primary-action';save.textContent='Save background';save.disabled=true;
    cancel.addEventListener('click',()=>void closeDialog(editor),{signal});
    save.addEventListener('click',async()=>{
      save.disabled=true;
      try{await write('/api/proxy/profile/me',{background_url:url,background_crop:values});await closeDialog(editor);showToast('Profile background saved.','success');}
      catch(error){if(!signal.aborted)showToast((error as Error).message,'error');}finally{save.disabled=false;}
    },{signal});
    buttons.append(cancel,save);editor.append(heading,frame,status,buttons);document.body.append(editor);
    editor.addEventListener('close',()=>{signal.removeEventListener('abort',cleanup);cleanup();},{once:true});
    editor.showModal();
    try{
      const {mountProfileCropper}=await import('./profile-artwork-cropper');
      if(lifetime.signal.aborted)return;
      await mountProfileCropper(frame,image,aspect,lifetime.signal,value=>{values=value;});
      if(lifetime.signal.aborted)return;
      status.hidden=true;save.disabled=false;
    }catch{if(!lifetime.signal.aborted){status.textContent='Couldn’t load this image. Close and try again.';save.disabled=true;}}

  },{signal});
  signal.addEventListener('abort',()=>actions.remove(),{once:true});
  return (url?:string)=>{if(url!==undefined)saved=url||undefined;paint();};
}
