import {showDialog,closeDialog} from './ui-motion';

export const watchRollbackWarning = 'Move watched progress back? All episodes after the new progress marker will be marked unwatched, and their recorded watch dates and evidence will be deleted.';
export const episodeRollbackWarning = 'Mark this episode and all later episodes unwatched? Their recorded watch dates and evidence will be deleted.';

/** Share the existing editor confirmation, including dismissal and page cleanup. */
export async function confirmEntryAction(message:string,signal:AbortSignal,options:{confirmLabel?:string}={}):Promise<boolean> {
  return (await chooseEntryAction(message,signal,options))==='confirm';
}

type Choice = 'confirm'|'alternative'|'cancel';
export function chooseEntryAction(message:string,signal:AbortSignal,
  options:{confirmLabel?:string;alternativeLabel?:string}={}):Promise<Choice> {
  const dialog=document.querySelector<HTMLDialogElement>('#editor-confirmation');
  if(signal.aborted||!dialog||dialog.open)return Promise.resolve('cancel');
  return new Promise(resolve=>{
    dialog.querySelector<HTMLElement>('#editor-confirmation-body')!.textContent=message;
    dialog.querySelector<HTMLButtonElement>('[value="confirm"]')!.textContent=options.confirmLabel||'OK';
    const alternative=dialog.querySelector<HTMLButtonElement>('[value="alternative"]')!;
    alternative.hidden=!options.alternativeLabel;
    alternative.textContent=options.alternativeLabel||'';
    dialog.querySelector<HTMLButtonElement>('.editor-confirmation-close')!.hidden=Boolean(options.alternativeLabel);
    dialog.returnValue='';
    const finish=(choice:Choice)=>{
      dialog.removeEventListener('close',closed);dialog.removeEventListener('click',backdrop);
      signal.removeEventListener('abort',aborted);
      dialog.returnValue='';
      resolve(choice);
    };
    const closed=()=>finish(dialog.returnValue==='confirm'?'confirm':
      dialog.returnValue==='alternative'&&options.alternativeLabel?'alternative':'cancel');
    const aborted=()=>{finish('cancel');if(dialog.open)dialog.close();};
    const backdrop=(event:Event)=>{if(event.target===dialog)void closeDialog(dialog,'cancel');};
    dialog.addEventListener('close',closed,{once:true});dialog.addEventListener('click',backdrop);
    signal.addEventListener('abort',aborted,{once:true});showDialog(dialog);
  });
}
