// Replay the short entrance only after a module has fully left the viewport.
export function initializeScrollMotion(
  root: HTMLElement,
  signal: AbortSignal,
  selector = ".lab-module",
  revealClass = "lab-scroll-reveal",
  options: { animateInitial?: boolean; downwardOnly?: boolean; scaleEntrance?: boolean; once?: boolean } = {},
) {
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  if (reduced.matches || signal.aborted) return;
  const mobile = matchMedia("(max-width: 650px), (pointer: coarse)");
  const pending = new Map<HTMLElement, Animation | null>();
  const entrances = new Set<Animation>();
  let modules: HTMLElement[] = [];
  const registered = new WeakSet<HTMLElement>();
  const appeared = new WeakSet<HTMLElement>();
  let frame = 0;
  let initialRegistration = true;
  let previousScrollY = scrollY;
  let scrollingUp = false;

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
    appeared.add(element);
    const animation = element.animate(
      [
        options.scaleEntrance ? { opacity: 0, scale: "0.7" } : { opacity: 0.2, translate: `0 ${mobile.matches ? 8 : 12}px` },
        options.scaleEntrance ? { opacity: 1, scale: "1" } : { opacity: 1, translate: "0 0" },
      ],
      { duration: options.scaleEntrance ? 160 : 260, easing: options.scaleEntrance ? "cubic-bezier(.2,.7,.2,1)" : "cubic-bezier(.25,.1,.25,1)", fill: "both" },
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
        appeared.add(element);
        if (bounds.bottom > 0 && (!initialRegistration || options.animateInitial !== false)) {
          const entrance = element.animate(
            [
              options.scaleEntrance ? { opacity: 0, scale: "0.7" } : { opacity: 0, transform: "translateY(7px)" },
              options.scaleEntrance ? { opacity: 1, scale: "1" } : { opacity: 1, transform: "translateY(0)" },
            ],
            { duration: options.scaleEntrance ? 160 : 260, easing: options.scaleEntrance ? "cubic-bezier(.2,.7,.2,1)" : "ease" },
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
  initialRegistration = false;

  const update = () => {
    frame = 0;
    if (scrollY !== previousScrollY) scrollingUp = scrollY < previousScrollY;
    previousScrollY = scrollY;
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
        // Measure the card's reserved box, not its temporarily scaled poster.
        const bounds = options.scaleEntrance
          ? (element.parentElement ?? element).getBoundingClientRect()
          : element.getBoundingClientRect();
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
      // Browse entrances come from the lower edge only. Returning through the
      // top edge, or reversing direction mid-fade, keeps content fully painted.
      if (options.downwardOnly && (moduleBottom <= viewportTop || (scrollingUp && top < bottom))) {
        appeared.add(element);
        complete(element);
        continue;
      }
      // Rearm only offscreen, so a partly visible module never flashes or
      // restarts. Keep focused controls readable even if scrolled out of view.
      if (moduleBottom <= viewportTop || top >= bottom) {
        if (options.once && appeared.has(element)) {
          complete(element);
          continue;
        }
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
