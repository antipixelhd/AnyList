// Replay the short entrance only after a module has fully left the viewport.
export function initializeScrollMotion(
  root: HTMLElement,
  signal: AbortSignal,
  selector = ".lab-module",
  revealClass = "lab-scroll-reveal",
) {
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  if (reduced.matches || signal.aborted) return;
  const mobile = matchMedia("(max-width: 650px), (pointer: coarse)");
  const pending = new Map<HTMLElement, Animation | null>();
  const entrances = new Set<Animation>();
  let modules: HTMLElement[] = [];
  const registered = new WeakSet<HTMLElement>();
  let frame = 0;

  const viewport = () => {
    const view = window.visualViewport;
    return {
      top: view?.offsetTop ?? 0,
      bottom: view ? view.offsetTop + view.height : innerHeight,
    };
  };
  const complete = (element: HTMLElement) => {
    pending.get(element)?.cancel();
    element.classList.remove(revealClass);
    pending.delete(element);
  };
  const start = (element: HTMLElement) => {
    const animation = element.animate(
      [
        { opacity: 0.2, translate: `0 ${mobile.matches ? 8 : 12}px` },
        { opacity: 1, translate: "0 0" },
      ],
      { duration: 260, easing: "cubic-bezier(.25,.1,.25,1)", fill: "both" },
    );
    pending.set(element, animation);
    animation.addEventListener("finish", () => complete(element), {
      once: true,
    });
  };

  const register = () => {
    modules = modules.filter((element) => {
      if (element.isConnected) return true;
      complete(element);
      return false;
    });
    root.querySelectorAll<HTMLElement>(selector).forEach((element) => {
      if (registered.has(element)) return;
      registered.add(element);
      modules.push(element);
      const bounds = element.getBoundingClientRect();
      if (element.getClientRects().length && bounds.top < viewport().bottom) {
        if (bounds.bottom > 0) {
          const entrance = element.animate(
            [
              { opacity: 0, transform: "translateY(7px)" },
              { opacity: 1, transform: "translateY(0)" },
            ],
            { duration: 260, easing: "ease" },
          );
          entrances.add(entrance);
          entrance.addEventListener(
            "finish",
            () => entrances.delete(entrance),
            { once: true },
          );
        }
        return;
      }
      // Set the starting appearance BEFORE entry, rather than resetting an
      // already visible component to transparent in an observer callback.
      element.classList.add(revealClass);
      pending.set(element, null);
    });
  };

  register();

  const update = () => {
    frame = 0;
    const { top: viewportTop, bottom } = viewport();
    // Batch position reads before starting/cancelling animations. Use the
    // unshifted box, so the entrance itself cannot move its trigger point.
    const positions = modules
      .filter((element) => element.isConnected)
      .map((element) => {
        const animation = pending.get(element);
        const visible = !!element.getClientRects().length;
        const shift =
          parseFloat(getComputedStyle(element).translate.split(" ")[1]) || 0;
        const bounds = element.getBoundingClientRect();
        return {
          element,
          animation,
          visible,
          top: bounds.top - shift,
          bottom: bounds.bottom - shift,
        };
      });
    for (const {
      element,
      animation,
      visible,
      top,
      bottom: moduleBottom,
    } of positions) {
      if (!visible) {
        if (animation) complete(element);
        continue;
      }
      // Rearm only offscreen, so a partly visible module never flashes or
      // restarts. Keep focused controls readable even if scrolled out of view.
      if (moduleBottom <= viewportTop || top >= bottom) {
        if (element.matches(":focus-within")) {
          complete(element);
          continue;
        }
        if (animation || !pending.has(element)) {
          complete(element);
          element.classList.add(revealClass);
          pending.set(element, null);
        }
        continue;
      }
      if (!pending.has(element)) continue;
      // Start just inside the viewport so the fade happens on screen. Let the
      // short animation finish even during a wheel step or swipe.
      if (top > bottom - 24 || moduleBottom < viewportTop + 24) continue;
      if (
        !animation &&
        !element.matches("[data-lab-pending], [data-lab-loading]")
      )
        start(element);
    }
  };
  const schedule = () => {
    if (!frame && modules.length && !reduced.matches && !signal.aborted)
      frame = requestAnimationFrame(update);
  };
  const finish = () => {
    cancelAnimationFrame(frame);
    frame = 0;
    for (const element of pending.keys()) complete(element);
    entrances.forEach((animation) => animation.cancel());
  };
  update();
  window.addEventListener("scroll", schedule, { passive: true, signal });
  window.addEventListener("resize", schedule, { signal });
  window.visualViewport?.addEventListener("resize", schedule, { signal });
  window.visualViewport?.addEventListener("scroll", schedule, {
    passive: true,
    signal,
  });
  // A viewport/input change must not leave an old desktop fade on mobile.
  mobile.addEventListener("change", finish, { signal });
  reduced.addEventListener(
    "change",
    () => {
      if (reduced.matches) finish();
    },
    { signal },
  );
  root.addEventListener(
    "focusin",
    (event) => {
      const element = (event.target as Element | null)?.closest<HTMLElement>(
        selector,
      );
      if (element && pending.has(element)) complete(element);
    },
    { signal },
  );
  // Loading completion can start a pending reveal, but cannot replay one.
  const visibility = new MutationObserver(() => {
    register();
    schedule();
  });
  visibility.observe(root, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ["hidden", "data-lab-pending", "data-lab-loading"],
  });
  const layout = new ResizeObserver(schedule);
  layout.observe(root);
  signal.addEventListener(
    "abort",
    () => {
      visibility.disconnect();
      layout.disconnect();
      finish();
    },
    { once: true },
  );
}
