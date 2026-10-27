import {statuses} from './tracking-statuses.ts';

/** Keep list groups and editor options in the same order across media domains. */
export const gameStatuses = statuses.map(([value,label]):readonly [string,string] =>
  value==='watching'?['playing','Playing']:value==='planning'?['planning','Planned']:[value,label]);

export function hoursInput(seconds:number|null|undefined):string {
  return seconds==null?'':Number((seconds/3600).toFixed(6)).toString();
}

export function achievementCountInput(raw:string,total:number|null|undefined):number {
  const value=Number(raw);
  if(!raw.trim()||!Number.isSafeInteger(value)||value<0||value>2147483647||total!=null&&value>total)
    throw new Error(total==null?'Enter a valid unlocked count.':'Enter an unlocked count within the known total.');
  return value;
}

export function actualSeconds(value:string):number|null {
  if(!value.trim())return null;
  const hours=Number(value);
  const seconds=Math.round(hours*3600);
  if(!Number.isFinite(hours)||hours<0||!Number.isSafeInteger(seconds))throw new Error('Enter a valid number of hours.');
  return seconds;
}

export function gameTimePatch(current:Record<string,any>,status:string,fields:{total:string;first:string;replays:string}) {
  const patch:Record<string,any>={};
  if(fields.total!==hoursInput(current.total_seconds))patch.total_seconds=actualSeconds(fields.total);
  if(status==='completed'&&(fields.first!==hoursInput(current.completion_seconds)||current.status!=='completed'&&current.completion_seconds==null))patch.completion_seconds=actualSeconds(fields.first);
  const replays=fields.replays.trim()===''?null:Number(fields.replays);
  if(replays!=null&&(!Number.isInteger(replays)||replays<0||replays>2147483647))throw new Error('Enter a whole replay count.');
  if(replays!==(current.replay_count??null))patch.replay_count=replays;
  return patch;
}

export function gameDatePatch(current:Record<string,any>,status:string,dates:Record<string,string>,touched:Set<string>,today:string) {
  const patch:Record<string,string|null>={};
  for(const name of ['start_date','finish_date']) {
    if(dates[name]!== (current[name]||'')||touched.has(name))patch[name]=dates[name]||null;
  }
  if(status!==current.status) {
    if(status==='playing'&&!current.start_date&&!('start_date' in patch))patch.start_date=today;
    if(status==='completed'&&!current.finish_date&&!('finish_date' in patch))patch.finish_date=today;
  }
  return patch;
}
