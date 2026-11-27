export interface ContributorBrowseCard {
  key: string;
  name: string;
  image: string | null;
  department: string | null;
  domain: 'movie' | 'series' | 'game' | null;
}

export interface ContributorBrowseResults {
  results: ContributorBrowseCard[];
  has_more: boolean;
}

export const staffRankings = [
  ['popular', 'Most popular'],
  ['birthdays', 'Birthdays today'],
] as const;

export const studioRankings = [
  ['credits', 'Most credited'],
  ['name', 'A–Z'],
] as const;

export const staffJobs = [
  ['', 'All jobs'],
  ['acting', 'Acting'],
  ['directing', 'Directing'],
  ['writing', 'Writing'],
  ['producing', 'Producing'],
  ['music', 'Music'],
  ['cinematography', 'Cinematography'],
  ['editing', 'Editing'],
] as const;
