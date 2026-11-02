import {showDialog,closeDialog} from './ui-motion';
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
  profile.addEventListener('click',()=>{
    const {url}=selected();if(!url)return;
    const editor=document.createElement('dialog');editor.className='profile-artwork-editor';editor.setAttribute('aria-label','Profile background');
    const heading=document.createElement('h2');heading.textContent='Profile background';
    const frame=document.createElement('div');frame.className='profile-artwork-frame';frame.style.aspectRatio=String(window.innerWidth/(window.innerWidth<=650?220:330));
    const image=document.createElement('img');image.src=url;image.alt='Profile background preview';frame.append(image);
    const values={x:50,y:50,zoom:1};
    const sliders=document.createElement('div');
    const render=()=>{image.style.objectPosition=`${values.x}% ${values.y}%`;image.style.transformOrigin=`${values.x}% ${values.y}%`;image.style.transform=`scale(${values.zoom})`;};
    for(const [key,label,min,max,step] of [['x','Horizontal position',0,100,1],['y','Vertical position',0,100,1],['zoom','Zoom',1,3,.01]] as const){
      const row=document.createElement('label');row.textContent=label;
      const input=document.createElement('input');input.type='range';input.min=String(min);input.max=String(max);input.step=String(step);input.value=String(values[key]);
      input.addEventListener('input',()=>{values[key]=Number(input.value);render();},{signal});row.append(input);sliders.append(row);
    }
    const buttons=document.createElement('div');buttons.className='profile-artwork-buttons';
    const cancel=document.createElement('button');cancel.type='button';cancel.textContent='Cancel';
    const save=document.createElement('button');save.type='button';save.className='gallery-primary-action';save.textContent='Save background';
    cancel.addEventListener('click',()=>void closeDialog(editor),{signal});
    save.addEventListener('click',async()=>{
      save.disabled=true;
      try{await write('/api/proxy/profile/me',{background_url:url,background_crop:values});await closeDialog(editor);showToast('Profile background saved.','success');}
      catch(error){if(!signal.aborted)showToast((error as Error).message,'error');}finally{save.disabled=false;}
    },{signal});
    buttons.append(cancel,save);editor.append(heading,frame,sliders,buttons);document.body.append(editor);
    editor.addEventListener('close',()=>editor.remove(),{once:true});
    signal.addEventListener('abort',()=>editor.remove(),{once:true});render();showDialog(editor);
  },{signal});
  signal.addEventListener('abort',()=>actions.remove(),{once:true});
  return (url?:string)=>{if(url!==undefined)saved=url||undefined;paint();};
}
