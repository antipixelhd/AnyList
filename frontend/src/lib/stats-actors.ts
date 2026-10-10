import { mountPeople } from './stats-people';
export const mountActors = (root: HTMLElement) => mountPeople(root, 'actors');
export const mountStaff = (root: HTMLElement) => mountPeople(root, 'staff');
