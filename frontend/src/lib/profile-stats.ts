import Chart from "chart.js/auto";
import type { ChartOptions } from "chart.js";
import type { ProfileStats } from "./profile-stats-data";
import { createProfileStatsLoader } from "./profile-stats-request";
function setupStats() {
  const root = document.querySelector<HTMLElement>("#profile-stats");
  if (root && root.dataset.statsInitialized !== "true") mountStats(root);
}

function mountStats(root: HTMLElement) {
  root.dataset.statsInitialized = "true";
  let data: ProfileStats = JSON.parse(root.dataset.initial || "{}");

  let media: ProfileStats["media_type"] = "all";
  const charts: Chart[] = [];
  const text = (name: string, value: string) => {
    const node = root.querySelector<HTMLElement>(`[data-stat="${name}"]`);
    if (node) node.textContent = value;
  };
  const duration = (minutes: number) =>
    minutes
      ? minutes >= 60
        ? `${Math.floor(minutes / 60).toLocaleString()}h ${minutes % 60}m`
        : `${minutes}m`
      : "0m";
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const typography = getComputedStyle(document.documentElement);
  Chart.defaults.color = "#8fa5bb";
  Chart.defaults.font.family = typography
    .getPropertyValue("--font-body")
    .trim();
  Chart.defaults.font.size =
    parseFloat(typography.fontSize) *
    parseFloat(typography.getPropertyValue("--type-meta-size"));
  Chart.defaults.font.weight = Number.parseInt(
    typography.getPropertyValue("--type-row-weight"),
    10,
  );
  function render(next: ProfileStats) {
    data = next;
    charts.splice(0).forEach((chart) => chart.destroy());
    text("current", String(data.current.total));
    text("titles", String(data.viewing.unique_titles));
    text(
      "episodes",
      `${data.viewing.unique_episodes} / ${data.viewing.unique_seasons}`,
    );
    text("seasons", "Unique episodes / seasons");
    text("time", duration(data.viewing.estimated_watch_minutes));
    text(
      "average",
      data.scores.average == null
        ? "—"
        : `${Number(data.scores.average).toFixed(1)} / 10`,
    );
    text(
      "rated",
      `${data.scores.rated} rated ${data.scores.rated === 1 ? "title" : "titles"}`,
    );
    text("repeats", String(data.viewing.repeat_views));
    const labels: { [key: string]: string } = {
      watching: "Watching",
      completed: "Completed",
      paused: "Paused",
      dropped: "Dropped",
      planning: "Plan to watch",
    };
    root.querySelector("[data-status-list]")!.innerHTML = Object.entries(
      data.current.statuses,
    )
      .map(
        ([key, value]) =>
          `<div><span>${labels[key] || key}</span><strong>${value}</strong><i style="--status-width:${data.current.total ? (Number(value) / data.current.total) * 100 : 0}%"></i></div>`,
      )
      .join("");
    const common: ChartOptions<"bar" | "line"> = {
      responsive: true,
      maintainAspectRatio: false,
      animation: reduced ? false : { duration: 220 },
      plugins: { legend: { display: false } },
    };
    charts.push(
      new Chart(root.querySelector<HTMLCanvasElement>("#score-chart")!, {
        type: "bar",
        data: {
          labels: data.scores.distribution.map((x) => x.score),
          datasets: [
            {
              data: data.scores.distribution.map((x) => x.count),
              backgroundColor: "#3db4f2",
              borderRadius: 3,
            },
          ],
        },
        options: {
          ...common,
          scales: {
            x: { grid: { display: false } },
            y: { beginAtZero: true, ticks: { precision: 0 } },
          },
        },
      }),
    );
    charts.push(
      new Chart(root.querySelector<HTMLCanvasElement>("#activity-chart")!, {
        type: "line",
        data: {
          labels: data.activity.map((x) => x.month),
          datasets: [
            {
              label: "Movies",
              data: data.activity.map((x) => x.movies),
              borderColor: "#3db4f2",
              backgroundColor: "#3db4f233",
              fill: true,
              tension: 0.3,
            },
            {
              label: "Episodes",
              data: data.activity.map((x) => x.episodes),
              borderColor: "#818cf8",
              backgroundColor: "#818cf822",
              fill: true,
              tension: 0.3,
            },
          ],
        },
        options: {
          ...common,
          plugins: { legend: { display: true, labels: { boxWidth: 10 } } },
          scales: {
            x: { grid: { display: false } },
            y: { beginAtZero: true, ticks: { precision: 0 } },
          },
        },
      }),
    );
    const genres = data.genres.slice(0, 8);
    charts.push(
      new Chart(root.querySelector<HTMLCanvasElement>("#genre-chart")!, {
        type: "bar",
        data: {
          labels: genres.map((x) => x.genre),
          datasets: [
            {
              data: genres.map((x) => x.count),
              backgroundColor: "#5d79df",
              borderRadius: 3,
            },
          ],
        },
        options: {
          ...common,
          indexAxis: "y",
          scales: {
            x: { beginAtZero: true, ticks: { precision: 0 } },
            y: { grid: { display: false } },
          },
        },
      }),
    );
    root.querySelector<HTMLElement>("[data-score-summary]")!.textContent = data
      .scores.rated
      ? `${data.scores.rated} current ratings with an average of ${Number(data.scores.average).toFixed(1)}.`
      : "No current ratings.";
    root.querySelector<HTMLElement>("[data-genre-summary]")!.textContent =
      genres.length
        ? genres.map((x) => `${x.genre} ${x.count}`).join(" · ")
        : "No genre data.";
  }
  const error = root.querySelector<HTMLElement>(".stats-error")!;
  const loader = createProfileStatsLoader(root.dataset.username!, {
    render,
    onError(message) {
      error.textContent = message;
      error.hidden = false;
    },
    onBusy(busy) {
      if (busy) {
        error.hidden = true;
        root.setAttribute("aria-busy", "true");
      } else root.removeAttribute("aria-busy");
    },
  });
  const load = () =>
    loader.load(
      media,
      root.querySelector<HTMLSelectElement>("#stats-year")!.value,
    );
  root
    .querySelectorAll<HTMLButtonElement>("[data-stats-media]")
    .forEach((button) =>
      button.addEventListener("click", () => {
        const value = button.dataset.statsMedia;
        if (value !== "all" && value !== "movie" && value !== "series") return;
        media = value;
        root
          .querySelectorAll("[data-stats-media]")
          .forEach((item) =>
            item.setAttribute("aria-pressed", String(item === button)),
          );
        load();
      }),
    );
  root.querySelector("#stats-year")?.addEventListener("change", load);
  render(data);
  document.addEventListener(
    "astro:before-swap",
    () => {
      loader.stop();
      charts.splice(0).forEach((chart) => chart.destroy());
    },
    { once: true },
  );
}
setupStats();
document.addEventListener("astro:after-swap", setupStats);
