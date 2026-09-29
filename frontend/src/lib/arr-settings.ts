export type ArrProvider = "radarr" | "sonarr";

export interface ArrProfiles {
  root_folders: { path: string; freeSpace: number }[];
  quality_profiles: { id: number; name: string }[];
  tags: { id: number; label: string }[];
}

type CredentialRequest = (
  path: string,
  credentials: Record<string, string>,
) => Promise<Response>;

function input(provider: ArrProvider, field: string, scope: ParentNode) {
  return scope.querySelector<HTMLInputElement>(`#${provider}_${field}`);
}

export function arrCredentials(
  provider: ArrProvider,
  scope: ParentNode = document,
) {
  return {
    url: input(provider, "url", scope)?.value || "",
    token: input(provider, "token", scope)?.value || "",
  };
}

export function selectedArrTags(
  provider: ArrProvider,
  scope: ParentNode = document,
): number[] {
  return Array.from(
    scope.querySelectorAll<HTMLInputElement>(
      `#${provider}_tags_list input[name="${provider}_tags"]`,
    ),
  )
    .filter((tag) => tag.type !== "checkbox" || tag.checked)
    .map((tag) => Number(tag.value))
    .filter((id) => Number.isInteger(id) && id >= 0);
}

export function arrSettings(
  provider: ArrProvider,
  scope: ParentNode = document,
) {
  const credentials = arrCredentials(provider, scope);
  const profile = scope.querySelector<HTMLSelectElement>(
    `#${provider}_quality_profile`,
  )?.value;
  return {
    [`${provider}_url`]: credentials.url || null,
    [`${provider}_token`]: credentials.token || null,
    [`${provider}_root_folder`]:
      scope.querySelector<HTMLSelectElement>(`#${provider}_root_folder`)
        ?.value || null,
    [`${provider}_quality_profile`]: profile ? Number(profile) : null,
    [`${provider}_tags`]: selectedArrTags(provider, scope),
    [`${provider}_customize_on_add`]:
      input(provider, "customize_on_add", scope)?.checked ?? false,
    ...(provider === "sonarr"
      ? {
          sonarr_season_folder:
            input(provider, "season_folder", scope)?.checked ?? true,
        }
      : {}),
  };
}

/** Only return profiles once both remote requests have succeeded. */
export async function fetchArrProfiles(
  provider: ArrProvider,
  credentials: Record<string, string>,
  request: CredentialRequest,
): Promise<ArrProfiles> {
  const name = provider === "radarr" ? "Radarr" : "Sonarr";
  for (const path of [`test-${provider}`, `${provider}/profiles`]) {
    const response = await request(`/api/proxy/auth/${path}`, credentials);
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(
        typeof data.detail === "string"
          ? data.detail
          : `Failed to load ${name} profiles (HTTP ${response.status})`,
      );
    }
    if (path.endsWith("/profiles")) return response.json();
  }
  throw new Error(`Failed to load ${name} profiles`);
}

function populateSelect(
  select: HTMLSelectElement | null,
  choices: { value: string; label: string }[],
  placeholder: string,
) {
  if (!select) return;
  const current = select.value;
  const options = [{ value: "", label: placeholder }, ...choices].map(
    (choice) => {
      const option = document.createElement("option");
      option.value = choice.value;
      option.textContent = choice.label;
      option.selected = choice.value === current;
      return option;
    },
  );
  select.replaceChildren(...options);
}

/** Replace remote choices while retaining the user's current form selections. */
export function renderArrProfiles(
  provider: ArrProvider,
  profiles: ArrProfiles,
  scope: ParentNode = document,
) {
  populateSelect(
    scope.querySelector<HTMLSelectElement>(`#${provider}_root_folder`),
    profiles.root_folders.map((folder) => ({
      value: folder.path,
      label: `${folder.path} (${(folder.freeSpace / 1024 ** 3).toFixed(1)} GB free)`,
    })),
    "-- Select Folder --",
  );
  populateSelect(
    scope.querySelector<HTMLSelectElement>(`#${provider}_quality_profile`),
    profiles.quality_profiles.map((profile) => ({
      value: String(profile.id),
      label: profile.name,
    })),
    "-- Select Profile --",
  );

  const container = scope.querySelector<HTMLElement>(`#${provider}_tags_list`);
  if (!container) return;
  const selected = selectedArrTags(provider, scope);
  const tags = profiles.tags.map((tag) => {
    const label = document.createElement("label");
    label.className =
      "group relative flex items-center gap-1.5 px-2 py-1 bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 rounded-md cursor-pointer transition-colors";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.name = `${provider}_tags`;
    checkbox.value = String(tag.id);
    checkbox.checked = selected.includes(tag.id);
    checkbox.className = "hidden tag-checkbox";
    const text = document.createElement("span");
    text.className =
      "text-xs font-medium text-zinc-300 group-hover:text-zinc-100";
    text.textContent = tag.label;
    const updateStyle = () => {
      for (const name of [
        "ring-1",
        "ring-blue-500",
        "border-blue-500/50",
        "bg-blue-500/10",
      ]) {
        label.classList.toggle(name, checkbox.checked);
      }
    };
    checkbox.addEventListener("change", updateStyle);
    updateStyle();
    label.append(checkbox, text);
    return label;
  });
  if (tags.length) container.replaceChildren(...tags);
  else {
    const empty = document.createElement("span");
    empty.className = "text-xs text-zinc-500 italic px-2 py-1";
    empty.textContent = "No tags found";
    container.replaceChildren(empty);
  }
}
