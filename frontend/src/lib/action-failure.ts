import {markFailureHandled} from './handled-failures.ts';
import {showToast} from './toasts.ts';

type FailureAction = 'open'|'progress'|'add'|'update'|'remove'|'rating'|'status'|'favorite'|'library'|'custom-list'|'follow'|'note'|'settings'|'connect'|'sync'|'favorite-order';
const messages: Record<FailureAction, {short:string; withTitle:(title:string)=>string}> = {
  open:{short:'Opening title failed.',withTitle:t=>`Failed to open ${t}.`},
  progress:{short:'Progress update failed.',withTitle:t=>`Progress update failed: ${t}`},
  add:{short:'Adding title failed.',withTitle:t=>`Failed to add ${t} to your list.`},
  update:{short:'Update failed.',withTitle:t=>`Update failed: ${t}`},
  remove:{short:'Removing title failed.',withTitle:t=>`Failed to remove ${t} from your list.`},
  rating:{short:'Rating update failed.',withTitle:t=>`Rating update failed: ${t}`},
  status:{short:'Status update failed.',withTitle:t=>`Status update failed: ${t}`},
  favorite:{short:'Favorites update failed.',withTitle:t=>`Favorites update failed: ${t}`},
  library:{short:'Library update failed.',withTitle:t=>`Library update failed: ${t}`},
  'custom-list':{short:'List update failed.',withTitle:t=>`Failed to update ${t}.`},
  'favorite-order':{short:'Failed to save favorite order.',withTitle:()=> 'Failed to save favorite order.'},
  follow:{short:'Following update failed.',withTitle:t=>`Following update failed: ${t}`},
  note:{short:'Note not saved.',withTitle:()=> 'Note not saved.'},
  settings:{short:'Settings not saved.',withTitle:()=> 'Settings not saved.'},
  connect:{short:'Connection failed.',withTitle:t=>`Connection to ${t} failed.`},
  sync:{short:'Sync failed.',withTitle:t=>`Sync with ${t} failed.`},
};
export function showActionFailure(action:FailureAction, title?:string|null, error?:unknown) {
  markFailureHandled(error);
  const definition=messages[action];
  const subject=title?.trim();
  return showToast({kind:'error',message:subject?definition.withTitle(subject):definition.short,
    shortMessage:definition.short,key:`action-failure:${action}:${subject || ''}`});
}
