export function nextStepperValue(value: number, direction: 1 | -1, increment: number, min: number, max: number): number {
  const steps = (Number.isFinite(value) ? value : min) / increment;
  const next = direction > 0 ? Math.floor(steps + 1e-9) + 1 : Math.ceil(steps - 1e-9) - 1;
  return Math.max(min, Math.min(max, Number((next * increment).toFixed(10))));
}

const bindings = new WeakMap<HTMLInputElement, AbortController>();

/** Apply the live field maximum before native validity checks or draft updates. */
export function capNumberInput(input: HTMLInputElement): void {
  if (input.disabled || input.readOnly || input.max === '') return;
  if (input.valueAsNumber > Number(input.max)) input.value = input.max;
}

/** Typed scores use tenths; their controls move by halves. Counts use their own step. */
export function setupScoreSteppers(root: ParentNode, signal?: AbortSignal): void {
  root.querySelectorAll<HTMLElement>('[data-score-stepper], [data-number-stepper]').forEach(stepper => {
    const input = stepper.querySelector<HTMLInputElement>('input[type="number"]');
    if (!input) return;
    bindings.get(input)?.abort();
    const controller = new AbortController();
    bindings.set(input, controller);
    signal?.addEventListener('abort', () => controller.abort(), {once:true, signal:controller.signal});

    const capValue = () => capNumberInput(input);
    input.addEventListener('input', capValue, {signal:controller.signal});
    input.addEventListener('change', capValue, {signal:controller.signal});

    const step = (direction: 1 | -1) => {
      if (input.disabled || input.readOnly) return;
      const increment = stepper.hasAttribute('data-score-stepper') ? .5 : Number(input.step) || 1;
      const min = input.min === '' ? 0 : Number(input.min);
      const max = input.max === '' ? Infinity : Number(input.max);
      const offset = Number(stepper.dataset.stepIncrement);
      const current = input.valueAsNumber;
      const next = offset
        ? Math.max(min, Math.min(max, Number(((Number.isFinite(current) ? current : min) + direction * offset).toFixed(10))))
        : nextStepperValue(current, direction, increment, min, max);
      input.value = String(next);
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
    };

    input.addEventListener('keydown', event => {
      if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
      event.preventDefault();
      step(event.key === 'ArrowUp' ? 1 : -1);
    }, {signal:controller.signal});
    stepper.querySelectorAll<HTMLButtonElement>('[data-score-step]').forEach(button => {
      button.addEventListener('click', () => step(button.dataset.scoreStep === 'up' ? 1 : -1), {signal:controller.signal});
    });
  });
}
