import {calendarDate,localToday,timestamp} from './date-time.ts';

type Duration = {days:number;hours:number;minutes:number};
const DurationFormat = (Intl as typeof Intl & {
  DurationFormat?:new (locale:undefined,options:{style:'narrow'})=>{format(duration:Duration):string};
}).DurationFormat;
const durationFormat = DurationFormat && new DurationFormat(undefined,{style:'narrow'});
const relativeDays = new Intl.RelativeTimeFormat(undefined,{numeric:'auto'});
const millisecondsPerDay = 86_400_000;
const millisecondsPerMinute = 60_000;

export function episodeCountdownLabel(position:string,dateTime:string,now=new Date()) {
  const airAt = timestamp(dateTime);
  let label = '';
  if (airAt) {
    const minutes = Math.ceil((airAt.getTime()-now.getTime())/millisecondsPerMinute);
    if (minutes<=0) return '';
    const duration = {days:Math.floor(minutes/1440),hours:Math.floor(minutes/60)%24,minutes:minutes%60};
    const formatted = durationFormat?.format(duration) ||
      Object.entries(duration).filter(([,value])=>value).map(([unit,value])=>`${value}${unit[0]}`).join(' ');
    label = `in ${formatted}`;
  } else {
    const day = calendarDate(dateTime);
    if (!day) return '';
    const days = (day.getTime()-calendarDate(localToday(now))!.getTime())/millisecondsPerDay;
    if (days<0) return '';
    label = relativeDays.format(days,'day');
  }
  return ` · ${position ? `${position} ` : ''}${label}`;
}

export function registerEpisodeCountdown() {
  if (customElements.get('episode-countdown')) return;
  customElements.define('episode-countdown',class extends HTMLElement {
    #timer?:number;

    connectedCallback() {
      const time = this.querySelector('time');
      if (!time) return;
      const airAt = timestamp(time.dateTime);
      const update = () => {
        const now = new Date();
        const label = episodeCountdownLabel(this.dataset.position||'',time.dateTime,now);
        if (time.textContent!==label) time.textContent=label;
        this.hidden = !label;
        if (!label) return;
        const untilNextMinute = airAt ? (airAt.getTime()-now.getTime())%millisecondsPerMinute : 0;
        this.#timer=window.setTimeout(update,untilNextMinute||millisecondsPerMinute);
      };
      update();
    }

    disconnectedCallback() {
      window.clearTimeout(this.#timer);
    }
  });
}
