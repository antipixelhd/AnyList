import { calendarDate, formatDate } from './date-time.ts';
export interface PersonDetail {
  name: string; image: string | null; biography: string | null; birthday: string | null; deathday: string | null;
  place_of_birth: string | null; roles: string[];
  works: {title: string; poster: string | null; href: string; release_date: string | null; roles: string[]}[];
}
function validDate(value: string | null): Date | null {
  return calendarDate(value);
}
export function personAge(birthday: string | null, deathday: string | null, today = new Date()): number | null {
  const born = validDate(birthday), end = deathday ? validDate(deathday) : today;
  if (!born || !end || born > end || end > today) return null;
  const beforeBirthday = end.getUTCMonth() < born.getUTCMonth() || (end.getUTCMonth() === born.getUTCMonth() && end.getUTCDate() < born.getUTCDate());
  return end.getUTCFullYear() - born.getUTCFullYear() - Number(beforeBirthday);
}
export function personDate(value: string | null) {
  return formatDate(value) || null;
}
