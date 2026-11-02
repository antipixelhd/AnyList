import {editProfileImage} from './profile-image-editor';

export function mountProfileImageUploads(root:HTMLElement) {
  const controller=new AbortController(),{signal}=controller;
  for(const section of root.querySelectorAll<HTMLElement>('[data-profile-image]')){
    const kind=section.dataset.profileImage!,avatar=kind==='avatar';
    const input=section.querySelector<HTMLInputElement>('[data-profile-file]')!,zone=section.querySelector<HTMLButtonElement>('[data-profile-upload]')!;
    const preview=section.querySelector<HTMLElement>('[data-profile-preview]')!,image=section.querySelector<HTMLImageElement>('[data-profile-preview-image]')!;
    const error=section.querySelector<HTMLElement>('[data-profile-image-error]')!;
    const report=(message='')=>{error.textContent=message;error.hidden=!message;};
    const paint=(url:string|null)=>{preview.hidden=!url;if(url){image.style.objectPosition='center';image.style.transform='';image.style.transformOrigin='';image.src=url;if(avatar){const appbar=document.querySelector('[data-appbar-avatar]');if(appbar){const next=document.createElement('img');next.src=url;next.alt='';appbar.replaceChildren(next);}}}else{image.removeAttribute('src');if(avatar){const appbar=document.querySelector('[data-appbar-avatar]');if(appbar)appbar.textContent=(root.closest<HTMLElement>('[data-preference-settings]')?.dataset.username||'?').slice(0,1).toUpperCase();}}};
    const upload=async(file:File)=>{
      report();
      if(!['image/jpeg','image/png','image/webp'].includes(file.type)){report('Choose a JPEG, PNG or WebP image.');return;}
      if(file.size>(avatar?3:6)*1024*1024){report(`Maximum upload size is ${avatar?3:6} MB.`);return;}
      zone.disabled=true;const url=URL.createObjectURL(file);
      try{await editProfileImage(url,avatar?'Avatar':'Profile banner',avatar?1:1700/330,signal,async(crop,editorSignal)=>{
        const body=new FormData();body.append('file',file);body.append('crop',JSON.stringify(crop));
        const response=await fetch(`/api/proxy/profile/me/${kind}`,{method:'POST',signal:editorSignal,body});
        const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Upload failed.');
        const stored=avatar?`/api/proxy${data.avatar_url}`:data.background_url;
        paint(`${stored}${stored.includes('?')?'&':'?'}v=${Date.now()}`);
      });}catch(cause){if(!signal.aborted)report((cause as Error).message);}
      finally{URL.revokeObjectURL(url);zone.disabled=false;input.value='';}
    };
    zone.addEventListener('click',()=>input.click(),{signal});
    input.addEventListener('change',()=>{if(input.files?.[0])void upload(input.files[0]);},{signal});
    for(const event of ['dragenter','dragover'])zone.addEventListener(event,e=>{e.preventDefault();zone.dataset.drag='true';},{signal});
    for(const event of ['dragleave','drop'])zone.addEventListener(event,e=>{e.preventDefault();delete zone.dataset.drag;},{signal});
    zone.addEventListener('drop',event=>{if(!zone.disabled&&event.dataTransfer?.files[0])void upload(event.dataTransfer.files[0]);},{signal});
    section.querySelector('[data-profile-remove]')!.addEventListener('click',async()=>{
      try{const response=await fetch(`/api/proxy/profile/me/${kind}`,{method:'DELETE',signal});if(!response.ok)throw new Error('Could not remove image.');paint(null);}
      catch(cause){if(!signal.aborted)report((cause as Error).message);}
    },{signal});
  }
  return ()=>controller.abort();
}
