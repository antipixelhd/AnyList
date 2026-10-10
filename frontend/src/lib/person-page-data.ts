export interface PersonDetail {
  name: string; image: string | null; biography: string | null; birthday: string | null; deathday: string | null;
  place_of_birth: string | null; roles: string[];
  works: {title: string; poster: string | null; href: string; release_date: string | null; roles: string[]}[];
}
function validDate(value: string | null): Date | null {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0,10) === value ? date : null;
}
export function personAge(birthday: string | null, deathday: string | null, today = new Date()): number | null {
  const born = validDate(birthday), end = deathday ? validDate(deathday) : today;
  if (!born || !end || born > end || end > today) return null;
  const beforeBirthday = end.getUTCMonth() < born.getUTCMonth() || (end.getUTCMonth() === born.getUTCMonth() && end.getUTCDate() < born.getUTCDate());
  return end.getUTCFullYear() - born.getUTCFullYear() - Number(beforeBirthday);
}
export function personDate(value: string | null) {
  const date = validDate(value);
  return date ? date.toLocaleDateString('en', {day:'numeric', month:'long', year:'numeric', timeZone:'UTC'}) : null;
}
