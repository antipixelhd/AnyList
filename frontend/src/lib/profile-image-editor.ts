import {closeDialog} from './ui-motion';
import {showToast} from './toasts';

export type ProfileFraming={x:number;y:number;zoom:number};

export function editProfileImage(url:string,title:string,aspect:number,signal:AbortSignal,
  saveImage:(crop:ProfileFraming,signal:AbortSignal)=>Promise<void>):Promise<boolean> {
  return new Promise(resolve=>{
    const editor=document.createElement('dialog');editor.className='profile-artwork-editor';editor.setAttribute('aria-label',title);
    const heading=document.createElement('h2');heading.textContent=title;
    const stage=document.createElement('div');stage.className='profile-artwork-stage';
    const frame=document.createElement('div');frame.className='profile-artwork-canvas';
    const image=document.createElement('img');image.src=url;image.alt=`${title} crop preview`;frame.append(image);
    let values:ProfileFraming={x:50,y:50,zoom:1},saved=false,saving=false;
    const lifetime=new AbortController();
    const cleanup=()=>{lifetime.abort();editor.remove();resolve(saved);};
    signal.addEventListener('abort',cleanup,{once:true});
    const status=document.createElement('p');status.className='profile-crop-status';status.textContent='Loading image…';
    const buttons=document.createElement('div');buttons.className='profile-artwork-buttons';
    const cancel=document.createElement('button');cancel.type='button';cancel.textContent='Cancel';
    const save=document.createElement('button');save.type='button';save.className='gallery-primary-action';save.textContent='Save image';save.disabled=true;
    cancel.addEventListener('click',()=>{if(!saving)void closeDialog(editor);},{signal:lifetime.signal});
    editor.addEventListener('cancel',event=>{if(saving)event.preventDefault();},{signal:lifetime.signal});
    save.addEventListener('click',async()=>{
      saving=true;save.disabled=true;cancel.disabled=true;
      try{await saveImage(values,lifetime.signal);saved=true;await closeDialog(editor);}
      catch(error){if(!lifetime.signal.aborted)showToast(error instanceof Error?error.message:'Could not save image.','error');}
      finally{saving=false;save.disabled=false;cancel.disabled=false;}
    },{signal:lifetime.signal});
    buttons.append(cancel,save);stage.append(frame,status);editor.append(heading,stage,buttons);document.body.append(editor);
    editor.addEventListener('close',()=>{signal.removeEventListener('abort',cleanup);cleanup();},{once:true});
    editor.showModal();
    void import('./profile-artwork-cropper').then(async({mountProfileCropper})=>{
      if(lifetime.signal.aborted)return;
      await mountProfileCropper(frame,image,aspect,lifetime.signal,value=>{values=value;});
      if(!lifetime.signal.aborted){status.hidden=true;save.disabled=false;}
    }).catch(()=>{if(!lifetime.signal.aborted)status.textContent='Couldn’t load this image. Close and try again.';});
  });
}
