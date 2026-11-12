import { mountPeople } from './stats-people';
export const mountActors = (root: HTMLElement, settleScroll: (card: HTMLElement) => void) => mountPeople(root, 'actors', settleScroll);
export const mountStaff = (root: HTMLElement, settleScroll: (card: HTMLElement) => void) => mountPeople(root, 'staff', settleScroll);
