import {
  subscribeNotifications,
  type NotificationSnapshot,
} from "./notification-store";
import {
  applyResponsiveArtwork,
  responsiveArtwork,
} from "./responsive-artwork";
import {
  eventBadge,
  eventCanChooseStatus,
  eventCanKeepPrevious,
  eventHref,
  eventIcon,
  eventImage,
  eventKey,
  eventMessage,
  eventSource,
  eventTitle,
  eventVariant,
  historyChanges,
  historyLabel,
  historyValue,
  isNewSeasonRelease,
  isSeasonReleaseNotification,
  notificationVersion,
  outboundKey,
  seasonArtworkFallback,
  seasonArtworkRetry,
  shouldAutoSee,
  statusLabel,
} from "./notification-presentation";

let pageListeners: AbortController | undefined;

document.addEventListener("astro:page-load", () => {
  pageListeners?.abort();
  const listeners = (pageListeners = new AbortController());
  const root = document.querySelector<HTMLElement>(".review-dashboard");
  const list = document.querySelector<HTMLElement>("#notification-list");
  const queue = document.querySelector<HTMLDetailsElement>(
    "#notification-queue",
  );
  const queueList = document.querySelector<HTMLElement>(
    "#notification-queue-list",
  );
  const eventTemplate = document.querySelector<HTMLTemplateElement>(
    "#notification-card-template",
  );
  const outboundTemplate = document.querySelector<HTMLTemplateElement>(
    "#notification-outbound-template",
  );
  const error = document.querySelector<HTMLElement>("#event-error");
  if (
    !root ||
    !list ||
    !queue ||
    !queueList ||
    !eventTemplate ||
    !outboundTemplate ||
    !error
  )
    return;

  mountInbox(
    { root, list, queue, queueList, eventTemplate, outboundTemplate, error },
    listeners,
  );
});

document.addEventListener("astro:before-swap", () => pageListeners?.abort());

interface InboxElements {
  root: HTMLElement;
  list: HTMLElement;
  queue: HTMLDetailsElement;
  queueList: HTMLElement;
  eventTemplate: HTMLTemplateElement;
  outboundTemplate: HTMLTemplateElement;
  error: HTMLElement;
}

function mountInbox(
  {
    root,
    list,
    queue,
    queueList,
    eventTemplate,
    outboundTemplate,
    error,
  }: InboxElements,
  listeners: AbortController,
) {
  let latest: NotificationSnapshot | null = null;
  const deferred = new Set<string>();
  const seen = new Set<number>();

  async function request(path: string, body: any = {}, method = "POST") {
    const response = await fetch(`/api/proxy/tracking/${path}`, {
      method,
      headers: { "Content-Type": "application/json" },
      body: method === "DELETE" ? undefined : JSON.stringify(body),
      signal: listeners.signal,
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(
        typeof data.detail === "string"
          ? data.detail
          : "Could not save this change.",
      );
    }
    const result = await response.json();
    listeners.signal.throwIfAborted();
    document.dispatchEvent(new Event("media-tracker:notifications-changed"));
    return result;
  }
  function fail(exception: unknown) {
    if (!listeners.signal.aborted) {
      error.textContent =
        exception instanceof Error
          ? exception.message
          : "Could not save this change.";
      error.hidden = false;
    }
  }
  function cloneIcon(name: string) {
    const template = document.querySelector<HTMLTemplateElement>(
      `#notification-icon-${name}`,
    );
    if (template?.content.firstElementChild)
      return template.content.firstElementChild.cloneNode(true) as HTMLElement;
    const fallback = document.createElement("span");
    fallback.className = "ui-icon";
    fallback.textContent = "•";
    return fallback;
  }
  function replaceClass(element: HTMLElement, prefix: string, value: string) {
    element.classList.forEach((name) => {
      if (name !== "notice-card" && name.startsWith(prefix))
        element.classList.remove(name);
    });
    element.classList.add(`${prefix}${value}`);
  }
  function setHidden(element: HTMLElement | null, hidden: boolean) {
    if (element) element.hidden = hidden;
  }
  function setLink(
    heading: HTMLElement,
    href: string | undefined,
    title: string,
  ) {
    heading.replaceChildren();
    if (href) {
      const link = document.createElement("a");
      link.href = href;
      link.textContent = title;
      heading.append(link);
    } else heading.textContent = title;
  }
  function updateVisual(
    card: HTMLElement,
    imagePath: string | null,
    iconName: string,
  ) {
    const visual = card.querySelector<HTMLElement>(".notice-visual");
    if (!visual) return;
    const image = imagePath
      ? responsiveArtwork(imagePath, {
          size: "w342",
          sizes: "(max-width:460px) 46px, (max-width:760px) 54px, 72px",
          route: "direct",
        })
      : null;
    visual.classList.toggle("has-image", Boolean(image?.src));
    if (image?.src) {
      const img = document.createElement("img");
      applyResponsiveArtwork(img, imagePath!, {
        size: "w342",
        sizes: "(max-width:460px) 46px, (max-width:760px) 54px, 72px",
        route: "direct",
      });
      img.alt = "";
      img.loading = "lazy";
      visual.replaceChildren(img);
    } else visual.replaceChildren(cloneIcon(iconName));
  }
  function updateMeta(
    card: HTMLElement,
    source: string | undefined,
    badge: string | undefined,
  ) {
    const content = card.querySelector<HTMLElement>(".notice-content");
    if (!content) return;
    let meta = content.querySelector<HTMLElement>(".notice-meta");
    if (!meta) {
      meta = document.createElement("div");
      meta.className = "notice-meta";
      content.prepend(meta);
    }
    meta.replaceChildren();
    if (source) {
      const sourceNode = document.createElement("span");
      sourceNode.textContent = source;
      meta.append(sourceNode);
    }
    if (badge) {
      const badgeNode = document.createElement("strong");
      badgeNode.textContent = badge;
      meta.append(badgeNode);
    }
    meta.hidden = !source && !badge;
  }
  function updateMessage(card: HTMLElement, message: string | undefined) {
    const content = card.querySelector<HTMLElement>(".notice-content");
    if (!content) return;
    let paragraph = content.querySelector<HTMLElement>(":scope > p");
    if (!paragraph && message) {
      paragraph = document.createElement("p");
      const detail = content.querySelector("[data-notification-detail]");
      content.insertBefore(
        paragraph,
        detail || content.querySelector(".notice-actions") || null,
      );
    }
    if (paragraph) {
      paragraph.textContent = message || "";
      paragraph.hidden = !message;
    }
  }
  function addArrow(parent: HTMLElement) {
    parent.append(cloneIcon("arrow-right"));
  }
  function updateEventDetail(card: HTMLElement, event: any) {
    const detail = card.querySelector<HTMLElement>(
      "[data-notification-detail]",
    );
    if (!detail) return;
    detail.replaceChildren();
    if (
      event.previous_status &&
      event.proposed_status &&
      event.previous_status !== event.proposed_status
    ) {
      const transition = document.createElement("div");
      transition.className = "status-transition";
      const previous = document.createElement("span");
      previous.textContent = statusLabel(event.previous_status);
      transition.append(previous);
      addArrow(transition);
      const proposed = document.createElement("strong");
      proposed.textContent = statusLabel(event.proposed_status);
      transition.append(proposed);
      detail.append(transition);
    }
    const changes = historyChanges(event);
    if (changes.length) {
      const history = document.createElement("div");
      history.className = "history-changes";
      changes.forEach((change: any) => {
        const row = document.createElement("div");
        const label = document.createElement("small");
        label.textContent = historyLabel(change.field);
        const previous = document.createElement("span");
        previous.textContent = historyValue(change.previous);
        const proposed = document.createElement("strong");
        proposed.textContent = historyValue(change.proposed);
        row.append(label, previous);
        addArrow(row);
        row.append(proposed);
        history.append(row);
      });
      detail.append(history);
    }
    if (event.kind === "rating_conflict") {
      const transition = document.createElement("div");
      transition.className = "status-transition";
      const previous = document.createElement("span");
      previous.textContent = Number(event.previous_score).toFixed(1);
      transition.append(previous);
      addArrow(transition);
      const proposed = document.createElement("strong");
      proposed.textContent = Number(event.proposed_score).toFixed(1);
      transition.append(proposed);
      if (event.season_number != null) {
        const season = document.createElement("small");
        season.textContent = `Season ${event.season_number}`;
        transition.append(season);
      }
      detail.append(transition);
    }
  }
  function bindEventAction(element: HTMLElement, event: any) {
    const id = String(event.id);
    const action = element.dataset.action;
    if (element.matches("[data-action]")) element.dataset.event = id;
    if (element.matches("[data-status-for]")) element.dataset.statusFor = id;
    if (element.matches("[data-dismiss]")) element.dataset.dismiss = id;
    if (element.matches("[data-match-search]"))
      element.dataset.matchSearch = id;
    if (element.matches("[data-match-results]"))
      element.dataset.matchResults = id;
    if (element.matches("[data-match-box]")) element.dataset.matchBox = id;
    if (element.matches("[data-match-box]"))
      element.dataset.kind =
        event.payload?.media_kind === "movies" ? "movie" : "series";
    if (action === "ignore")
      element.textContent = `Ignore for ${event.provider || "this provider"}`;
    if (action === "confirm") {
      element.dataset.proposedStatus = event.proposed_status || "";
      element.dataset.statusChoice = String(eventCanChooseStatus(event));
    }
    if (action === "confirm")
      element.textContent =
        event.kind === "deletion_conflict"
          ? "Retry deletion"
          : event.kind?.startsWith("initial_")
            ? "Approve import"
            : event.kind === "rating_conflict"
              ? "Use provider score"
              : "Confirm";
  }
  function updateEventActions(card: HTMLElement, event: any) {
    const action = (name: string) =>
      card.querySelector<HTMLElement>(`[data-notification-action="${name}"]`);
    const visible = (name: string, show: boolean) =>
      setHidden(action(name), !show);
    visible("resolve", event.kind === "connection_failure");
    const resolve = action("resolve") as HTMLAnchorElement | null;
    if (resolve) resolve.href = event.payload?.resolve_url || "#";
    visible("rate", event.kind === "rating_needed" && Boolean(event.media));
    const rate = action("rate") as HTMLButtonElement | null;
    if (rate && event.media) {
      rate.dataset.quickRateId = String(event.media.id);
      rate.dataset.quickRateTitle = event.media.title || "";
      rate.dataset.quickRatePoster =
        responsiveArtwork(event.media.poster, {
          size: "w185",
          sizes: "42px",
          route: "direct",
        })?.src || "";
      rate.dataset.quickRateScore = "";
    }
    const match = action("match");
    const isMatch =
      event.state === "pending" && event.kind === "unmatched_import";
    visible("match", isMatch);
    if (match && isMatch) {
      bindEventAction(match, event);
      const input = match.querySelector<HTMLInputElement>("input");
      if (input && !input.dataset.interactionDirty)
        input.value = event.payload?.title || "";
      const search = match.querySelector<HTMLElement>("[data-match-search]");
      if (search) bindEventAction(search, event);
      const results = match.querySelector<HTMLElement>("[data-match-results]");
      if (results) bindEventAction(results, event);
      const ignore = match.querySelector<HTMLElement>('[data-action="ignore"]');
      if (ignore) bindEventAction(ignore, event);
    }
    const seasonRelease =
      isNewSeasonRelease(event) && event.state === "pending";
    visible("season-watching", seasonRelease);
    visible("season-planning", seasonRelease);
    ["season-watching", "season-planning"].forEach((name) => {
      const control = action(name);
      if (control && seasonRelease) bindEventAction(control, event);
    });
    const pending =
      event.state === "pending" &&
      ![
        "unmatched_import",
        "connection_failure",
        "new_season_release_date",
        "new_season_release",
      ].includes(event.kind);
    visible("confirm", pending);
    visible("status", pending && eventCanChooseStatus(event));
    visible(
      "keep",
      pending &&
        !isSeasonReleaseNotification(event) &&
        eventCanKeepPrevious(event),
    );
    visible("keep-rating", pending && event.kind === "rating_conflict");
    visible("keep-deletion", pending && event.kind === "deletion_conflict");
    ["confirm", "keep", "keep-rating", "keep-deletion"].forEach((name) => {
      const control = action(name);
      if (control) bindEventAction(control, event);
    });
    const select = action("status")?.querySelector<HTMLSelectElement>("select");
    if (select) {
      select.dataset.statusFor = String(event.id);
      Array.from(select.options).forEach((option) => {
        option.selected = option.value === event.proposed_status;
      });
    }
    visible("dismiss", Boolean(event.dismissible));
    const dismiss = action("dismiss");
    if (dismiss) dismiss.dataset.dismiss = String(event.id);
  }
  function populateEvent(card: HTMLElement, event: any) {
    const key = eventKey(event);
    card.dataset.notificationKey = key;
    card.dataset.notificationVersion = notificationVersion(event, "event");
    card.dataset.notificationKind = event.kind || "";
    card.dataset.notificationPriority = event.priority || "";
    card.dataset.notificationState = event.state || "";
    card.dataset.seasonArtworkFallback = String(seasonArtworkFallback(event));
    card.dataset.seasonArtworkRetry = String(seasonArtworkRetry(event));
    replaceClass(card, "notice-", eventVariant(event));
    updateVisual(card, eventImage(event), eventIcon(event));
    updateMeta(card, eventSource(event), eventBadge(event));
    setLink(
      card.querySelector<HTMLElement>(".notice-content h2")!,
      eventHref(event),
      eventTitle(event),
    );
    updateMessage(card, eventMessage(event));
    updateEventDetail(card, event);
    updateEventActions(card, event);
    return card;
  }
  function updateOutboundDetail(card: HTMLElement, item: any) {
    const detail = card.querySelector<HTMLElement>(
      "[data-notification-detail]",
    );
    if (!detail) return;
    detail.replaceChildren();
    const list = document.createElement("ul");
    list.className = "delivery-list";
    (item.deliveries || []).forEach((delivery: any) => {
      const row = document.createElement("li");
      const connection = document.createElement("strong");
      connection.textContent = delivery.connection || "Connected service";
      const state = document.createElement("span");
      state.textContent =
        delivery.state === "conflict"
          ? "Review needed"
          : delivery.error
            ? "Retry needed"
            : "Queued";
      row.append(connection, state);
      if (delivery.error) {
        const failure = document.createElement("small");
        failure.append(document.createTextNode(delivery.error));
        if (delivery.attempts > 1) {
          const attempts = document.createElement("span");
          attempts.textContent = ` · ${delivery.attempts} attempts`;
          failure.append(attempts);
        }
        row.append(failure);
      }
      list.append(row);
    });
    detail.append(list);
  }
  function populateOutbound(card: HTMLElement, item: any) {
    const key = outboundKey(item);
    card.dataset.notificationKey = key;
    card.dataset.notificationVersion = notificationVersion(item, "outbound");
    card.dataset.notificationKind = "outbound";
    card.dataset.notificationState = item.state || "";
    replaceClass(
      card,
      "notice-",
      item.state === "conflict" ? "warning" : "info",
    );
    updateVisual(card, item.poster || null, "arrows-rotate");
    updateMeta(
      card,
      "Connected services",
      item.state === "conflict" ? "Needs review" : "Pending",
    );
    setLink(
      card.querySelector<HTMLElement>(".notice-content h2")!,
      item.media_id ? `/title/${item.media_id}` : undefined,
      item.title || "Pending connection update",
    );
    updateMessage(card, undefined);
    updateOutboundDetail(card, item);
    return card;
  }
  function clone(template: HTMLTemplateElement) {
    return template.content.firstElementChild!.cloneNode(true) as HTMLElement;
  }
  function preserve(card: HTMLElement) {
    if (card.contains(document.activeElement)) return true;
    if (card.querySelector('[data-interaction-dirty="true"]')) return true;
    return card.dataset.quickRatingActive === "true";
  }
  function sortCards(container: HTMLElement, order: string[]) {
    const current = Array.from(
      container.querySelectorAll<HTMLElement>("[data-notification-key]"),
    ).map((card) => card.dataset.notificationKey || "");
    const extras = current.filter((key) => !order.includes(key));
    const desired = [...order, ...extras];
    if (
      current.length === desired.length &&
      current.every((key, index) => key === desired[index])
    )
      return;
    if (container.contains(document.activeElement)) return;
    const cards = new Map(
      Array.from(
        container.querySelectorAll<HTMLElement>("[data-notification-key]"),
      ).map((card) => [card.dataset.notificationKey || "", card]),
    );
    desired.forEach((key) => {
      const card = cards.get(key);
      if (card) container.append(card);
    });
  }
  function reconcileEvents(snapshot: NotificationSnapshot) {
    const incoming = new Map(
      (snapshot.results || []).map((event: any) => [eventKey(event), event]),
    );
    Array.from(
      list.querySelectorAll<HTMLElement>("[data-notification-key]"),
    ).forEach((card) => {
      const key = card.dataset.notificationKey || "";
      if (incoming.has(key)) return;
      if (card.dataset.notificationKeep === "true") return;
      if (preserve(card)) {
        deferred.add(key);
        card.dataset.notificationPendingRemoval = "true";
      } else card.remove();
    });
    const order: string[] = [];
    for (const event of snapshot.results || []) {
      const key = eventKey(event);
      order.push(key);
      const existing = Array.from(
        list.querySelectorAll<HTMLElement>("[data-notification-key]"),
      ).find((card) => card.dataset.notificationKey === key);
      const version = notificationVersion(event, "event");
      if (!existing) {
        list.append(populateEvent(clone(eventTemplate), event));
        continue;
      }
      if (
        existing.dataset.notificationVersion === version &&
        existing.dataset.notificationPendingRemoval !== "true"
      )
        continue;
      if (preserve(existing)) {
        deferred.add(key);
        existing.dataset.notificationPendingVersion = version;
        existing.dataset.notificationPendingRemoval = "";
        continue;
      }
      existing.replaceWith(populateEvent(clone(eventTemplate), event));
    }
    sortCards(list, order);
    list.hidden = !list.querySelector("[data-notification-key]");
    const lowPriority = (snapshot.results || []).filter(
      (event: any) => shouldAutoSee(event) && !seen.has(Number(event.id)),
    );
    lowPriority.forEach((event: any) => {
      seen.add(Number(event.id));
      const card = Array.from(
        list.querySelectorAll<HTMLElement>("[data-notification-key]"),
      ).find(
        (candidate) => candidate.dataset.notificationKey === eventKey(event),
      );
      if (card) {
        card.dataset.notificationKeep = "true";
        card.dataset.autoSeen = String(event.id);
      }
    });
    if (lowPriority.length)
      fetch("/api/proxy/tracking/recent-events/seen", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ids: lowPriority.map((event: any) => event.id),
        }),
        signal: listeners.signal,
      }).catch(() => {});
  }
  function reconcileOutbound(snapshot: NotificationSnapshot) {
    const incoming = new Map(
      (snapshot.outbound || []).map((item: any) => [outboundKey(item), item]),
    );
    Array.from(
      queueList.querySelectorAll<HTMLElement>("[data-notification-key]"),
    ).forEach((card) => {
      const key = card.dataset.notificationKey || "";
      if (incoming.has(key)) return;
      if (preserve(card)) {
        deferred.add(key);
        card.dataset.notificationPendingRemoval = "true";
      } else card.remove();
    });
    const order: string[] = [];
    for (const item of snapshot.outbound || []) {
      const key = outboundKey(item);
      order.push(key);
      const existing = Array.from(
        queueList.querySelectorAll<HTMLElement>("[data-notification-key]"),
      ).find((card) => card.dataset.notificationKey === key);
      const version = notificationVersion(item, "outbound");
      if (!existing) {
        queueList.append(populateOutbound(clone(outboundTemplate), item));
        continue;
      }
      if (
        existing.dataset.notificationVersion === version &&
        existing.dataset.notificationPendingRemoval !== "true"
      )
        continue;
      if (preserve(existing)) {
        deferred.add(key);
        existing.dataset.notificationPendingVersion = version;
        existing.dataset.notificationPendingRemoval = "";
        continue;
      }
      existing.replaceWith(populateOutbound(clone(outboundTemplate), item));
    }
    sortCards(queueList, order);
    queue.hidden =
      !(snapshot.outbound || []).length &&
      !queueList.querySelector("[data-notification-key]");
    const count = document.querySelector<HTMLElement>(
      "#notification-queue-count",
    );
    if (count) count.textContent = String((snapshot.outbound || []).length);
  }
  function updateEmptyState() {
    const empty = document.querySelector<HTMLElement>("#notification-empty");
    const hasCards = Boolean(list.querySelector("[data-notification-key]"));
    const hasOutbound = Boolean(
      queueList.querySelector("[data-notification-key]"),
    );
    list.hidden = !hasCards;
    if (empty)
      empty.hidden = hasCards || hasOutbound || Boolean(error && !error.hidden);
  }
  function applySnapshot(snapshot: NotificationSnapshot) {
    if (!snapshot || listeners.signal.aborted) return;
    latest = snapshot;
    const count = document.querySelector<HTMLElement>("#notification-count");
    if (count) {
      count.textContent =
        snapshot.pending > 99 ? "99+" : String(snapshot.pending || "");
      count.hidden = !snapshot.pending;
    }
    reconcileEvents(snapshot);
    reconcileOutbound(snapshot);
    error.hidden = true;
    updateEmptyState();
  }
  function flushDeferred() {
    if (!latest) return;
    Array.from(deferred).forEach((key) => {
      const card = Array.from(
        document.querySelectorAll<HTMLElement>("[data-notification-key]"),
      ).find((candidate) => candidate.dataset.notificationKey === key);
      if (card && preserve(card)) return;
      deferred.delete(key);
    });
    if (deferred.size < 1) applySnapshot(latest);
  }

  root.addEventListener(
    "input",
    (event) => {
      const input = (event.target as HTMLElement).closest<HTMLInputElement>(
        "input",
      );
      if (input && input.closest("[data-notification-key]"))
        input.dataset.interactionDirty = "true";
    },
    { signal: listeners.signal },
  );
  root.addEventListener(
    "focusout",
    (event) =>
      setTimeout(() => {
        const card = (event.target as HTMLElement).closest<HTMLElement>(
          "[data-notification-key]",
        );
        if (card && !card.contains(document.activeElement))
          card
            .querySelectorAll<HTMLElement>("[data-interaction-dirty]")
            .forEach((input) => delete input.dataset.interactionDirty);
        flushDeferred();
      }, 0),
    { signal: listeners.signal },
  );
  root.addEventListener(
    "click",
    async (event) => {
      const target = (event.target as HTMLElement).closest<HTMLElement>(
        "[data-event], [data-match-search], [data-match-result-event], [data-dismiss], [data-unignore]",
      );
      if (!target) return;
      if (target.dataset.matchSearch != null) {
        const box = target.closest<HTMLElement>("[data-match-box]");
        const input = box?.querySelector<HTMLInputElement>("input");
        const results = box?.querySelector<HTMLElement>("[data-match-results]");
        if (!box || !input || !results) return;
        event.preventDefault();
        target.setAttribute("aria-busy", "true");
        (target as HTMLButtonElement).disabled = true;
        results.textContent = "Searching…";
        try {
          const response = await fetch(
            `/api/proxy/tracking/catalog?q=${encodeURIComponent(input.value)}&media_type=${box.dataset.kind}`,
            { cache: "no-store", signal: listeners.signal },
          );
          if (!response.ok) throw new Error("Catalogue search failed.");
          const data = await response.json();
          results.replaceChildren(
            ...(data.results || []).slice(0, 8).map((item: any) => {
              const result = document.createElement("button");
              result.className = "match-result";
              result.type = "button";
              result.dataset.matchResultEvent = box.dataset.matchBox || "";
              result.dataset.matchResultMediaId = item.id || "";
              result.dataset.matchResultTmdbId = item.tmdb_id || "";
              result.dataset.matchResultKind = box.dataset.kind || "series";
              result.textContent = `${item.title}${item.year ? ` (${item.year})` : ""}`;
              return result;
            }),
          );
          if (!data.results?.length)
            results.textContent = "No catalogue titles found.";
          input.dataset.interactionDirty = "";
        } catch (exception) {
          if ((exception as Error).name !== "AbortError") {
            fail(exception);
            results.textContent = "";
          }
        } finally {
          (target as HTMLButtonElement).disabled = false;
          target.removeAttribute("aria-busy");
        }
        return;
      }
      if (target.dataset.matchResultEvent != null) {
        event.preventDefault();
        const id = target.dataset.matchResultEvent;
        const button = target as HTMLButtonElement;
        button.disabled = true;
        try {
          let mediaId = button.dataset.matchResultMediaId;
          if (!mediaId) {
            const imported = await request(
              `catalog/${button.dataset.matchResultKind}/${button.dataset.matchResultTmdbId}`,
            );
            mediaId = imported.id;
          }
          await request(`recent-events/${id}`, {
            action: "match",
            media_id: Number(mediaId),
          });
          button.blur();
          flushDeferred();
        } catch (exception) {
          fail(exception);
          button.disabled = false;
        }
        return;
      }
      if (target.dataset.event != null) {
        event.preventDefault();
        const button = target as HTMLButtonElement;
        if (button.disabled) return;
        button.disabled = true;
        const id = button.dataset.event!;
        const card = button.closest<HTMLElement>("[data-notification-key]");
        const status = card?.querySelector<HTMLSelectElement>(
          `[data-status-for="${CSS.escape(id)}"]`,
        )?.value;
        try {
          await request(`recent-events/${id}`, {
            action:
              button.dataset.action === "confirm" &&
              button.dataset.statusChoice === "true" &&
              status &&
              status !== button.dataset.proposedStatus
                ? "change"
                : button.dataset.action,
            status:
              button.dataset.action === "confirm" &&
              button.dataset.statusChoice === "true"
                ? status
                : undefined,
          });
          button.blur();
          flushDeferred();
        } catch (exception) {
          fail(exception);
          button.disabled = false;
        }
        return;
      }
      if (target.dataset.dismiss != null) {
        event.preventDefault();
        const button = target as HTMLButtonElement;
        button.disabled = true;
        const card = button.closest<HTMLElement>("[data-notification-key]");
        if (card) card.dataset.notificationKeep = "false";
        try {
          await request(
            `recent-events/${button.dataset.dismiss}`,
            {},
            "DELETE",
          );
          button.blur();
          flushDeferred();
        } catch (exception) {
          fail(exception);
          button.disabled = false;
        }
        return;
      }
      if (target.dataset.unignore != null) {
        const button = target as HTMLButtonElement;
        button.disabled = true;
        try {
          await request(
            `provider-ignores/${button.dataset.unignore}`,
            {},
            "DELETE",
          );
          button.closest(".ignore-list > div")?.remove();
        } catch (exception) {
          fail(exception);
          button.disabled = false;
        }
      }
    },
    { signal: listeners.signal },
  );
  root.addEventListener(
    "change",
    async (event) => {
      const input = (event.target as HTMLElement).closest<
        HTMLInputElement | HTMLSelectElement
      >("[data-pref]");
      if (!input) return;
      const value =
        input instanceof HTMLInputElement ? input.checked : Number(input.value);
      try {
        await request("preferences", { [input.dataset.pref!]: value }, "PATCH");
        if (input.dataset.pref === "show_new_ratings_popup")
          document.dispatchEvent(
            new CustomEvent("anylist:rating-popup-preference", {
              detail: { enabled: value },
            }),
          );
      } catch (exception) {
        fail(exception);
        if (input instanceof HTMLInputElement) input.checked = !value;
      }
    },
    { signal: listeners.signal },
  );
  root.addEventListener(
    "click",
    (event) => {
      const rate = (event.target as HTMLElement).closest<HTMLElement>(
        "[data-quick-rate-id]",
      );
      if (rate) {
        root
          .querySelectorAll<HTMLElement>("[data-quick-rating-active]")
          .forEach((card) => delete card.dataset.quickRatingActive);
        rate
          .closest<HTMLElement>("[data-notification-key]")
          ?.setAttribute("data-quick-rating-active", "true");
      }
    },
    { signal: listeners.signal },
  );
  document.querySelector<HTMLDialogElement>("#quick-rating")?.addEventListener(
    "close",
    () => {
      root
        .querySelectorAll<HTMLElement>("[data-quick-rating-active]")
        .forEach((card) => delete card.dataset.quickRatingActive);
      flushDeferred();
    },
    { signal: listeners.signal },
  );
  subscribeNotifications(applySnapshot, { signal: listeners.signal });
}
