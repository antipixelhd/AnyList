// Leave enough room for labels and touch targets instead of squeezing categories.
export function chartMinimumWidth(categories: number) {
  return Math.max(0, categories) * 44 + 32;
}
