type DecisionSteps = {matches: boolean; progress: boolean; ratings: boolean};

/** Final review is always explicit; Back never lands on an empty step. */
export function importDecisionStep(start: number, direction: 1 | -1, decisions: DecisionSteps): number | null {
  const available = [false, false, decisions.matches, decisions.progress, decisions.ratings, true];
  for (let step = start; step >= 2 && step <= 5; step += direction) {
    if (available[step]) return step;
  }
  return direction === 1 ? 5 : null;
}
