/** Typed scores use tenths; arrow controls move to the next half-point. */
export function setupScoreSteppers(root: ParentNode): void {
  root.querySelectorAll<HTMLElement>('[data-score-stepper]').forEach(stepper => {
    const input = stepper.querySelector<HTMLInputElement>('input[type="number"]');
    if (!input) return;

    const step = (direction: 1 | -1) => {
      const current = Number.isFinite(input.valueAsNumber) ? input.valueAsNumber : 0;
      const halfSteps = current * 2;
      const next = direction > 0
        ? (Math.floor(halfSteps + 1e-9) + 1) / 2
        : (Math.ceil(halfSteps - 1e-9) - 1) / 2;
      input.value = String(Math.max(0, Math.min(10, next)));
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
    };

    input.addEventListener('keydown', event => {
      if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
      event.preventDefault();
      step(event.key === 'ArrowUp' ? 1 : -1);
    });
    stepper.querySelectorAll<HTMLButtonElement>('[data-score-step]').forEach(button => {
      button.addEventListener('click', () => step(button.dataset.scoreStep === 'up' ? 1 : -1));
    });
  });
}
