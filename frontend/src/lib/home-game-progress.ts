import type {EditorTitle} from './editor-store';
import {gameProgress} from './game-progress';
import {scoreLabel} from './tracker';

/** Keep mounted cards and decoded art while optimistic writes settle. */
export function paintHomeGameProgress(shelf:HTMLElement,title:EditorTitle) {
  if(title.type!=='game')return;
  for(const card of shelf.querySelectorAll<HTMLElement>('[data-game-playing-card]')){
    if(Number(card.dataset.gamePlayingCard)!==title.id)continue;
    card.hidden=!title.entry||title.entry.status!=='playing';
    const progress=gameProgress(title.entry);
    const label=card.querySelector<HTMLElement>('.progress-card-progress-label');
    if(label)label.textContent=[progress.hours,shelf.dataset.showAchievements!=='false'?progress.achievement:null,
      title.entry?.score?`${scoreLabel(title.entry.score)} / 10`:null].filter(Boolean).join(' · ');
  }
}
