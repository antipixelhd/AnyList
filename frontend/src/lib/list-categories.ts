export type ListCategory = {
  id: string;
  label: string;
  default_label: string;
  description: string;
  hidden: boolean;
  can_rename: boolean;
  can_hide: boolean;
};
export type CategoryPresentation = {revision: number; categories: ListCategory[]};

const definitions = [
  ['playing', 'In Progress', 'Currently playing, usually toward a main objective.'],
  ['completed', 'Completed', 'Achieved your main objective.'],
  ['played', 'Played', 'Played without marking as completed, including endless games and one-off tries.'],
  ['paused', 'Paused', 'Set aside with the intention of returning.'],
  ['dropped', 'Dropped', 'Stopped with no intention of returning.'],
  ['planning', 'Planning', 'Wishlist or backlog games.'],
];

export function defaultGameCategories(): CategoryPresentation {
  return {revision: 0, categories: definitions.map(([id, label, description]) => ({
    id, label, default_label: label, description, hidden: false,
    can_rename: id !== 'completed', can_hide: !['completed', 'planning'].includes(id),
  }))};
}

export function categoryChoices(presentation: CategoryPresentation, current?: string): [string, string][] {
  return presentation.categories.filter(category => !category.hidden || category.id === current)
    .map(category => [category.id, category.label]);
}

export function categoryName(presentation: CategoryPresentation, id: string) {
  return presentation.categories.find(category => category.id === id)?.label || id;
}
