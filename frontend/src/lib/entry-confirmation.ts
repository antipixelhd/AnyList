import {showDialog,closeDialog} from './ui-motion';

export const watchRollbackWarning = 'Move watched progress back? All episodes after the new progress marker will be marked unwatched, and their recorded watch dates and evidence will be deleted.';
export const episodeRollbackWarning = 'Mark this episode and all later episodes unwatched? Their recorded watch dates and evidence will be deleted.';

/** Share the existing editor confirmation, including dismissal and page cleanup. */
export function confirmEntryAction(message:string,signal:AbortSignal):Promise<boolean> {
  const dialog=document.querySelector<HTMLDialogElement>('#editor-confirmation');
  if(signal.aborted||!dialog||dialog.open)return Promise.resolve(false);
  return new Promise(resolve=>{
    dialog.querySelector<HTMLElement>('#editor-confirmation-body')!.textContent=message;
    dialog.returnValue='cancel';
    const finish=(accepted:boolean)=>{
      dialog.removeEventListener('close',closed);dialog.removeEventListener('click',backdrop);
      signal.removeEventListener('abort',aborted);resolve(accepted);
    };
    const closed=()=>finish(dialog.returnValue==='confirm');
    const aborted=()=>{finish(false);if(dialog.open)dialog.close('cancel');};
    const backdrop=(event:Event)=>{if(event.target===dialog)void closeDialog(dialog,'cancel');};
    dialog.addEventListener('close',closed,{once:true});dialog.addEventListener('click',backdrop);
    signal.addEventListener('abort',aborted,{once:true});showDialog(dialog);
  });
}
