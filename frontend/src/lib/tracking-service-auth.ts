import { createDeviceAuthorization } from "./device-authorization";
import { errorMessage, showMessage } from "./settings-feedback";

const providers = [
  { id: "trakt", name: "Trakt", method: "device", credentialLabel: "tokens", credentialKeys: ["trakt_client_id", "trakt_client_secret"] },
  { id: "simkl", name: "Simkl", method: "pin", credentialLabel: "token", credentialKeys: ["simkl_client_id"] },
  { id: "mdblist", name: "MDBList", method: null, credentialLabel: "API key", credentialKeys: [] },
] as const;

let dispose: (() => void) | undefined;

function mount() {
  dispose?.();
  if (!document.querySelector("[data-connections-page]")) return;
  const token = document.getElementById("app-data")?.dataset.token ?? "";
  const lifetime = new AbortController();
  const sessions: ReturnType<typeof createDeviceAuthorization>[] = [];
  dispose = () => {
    lifetime.abort();
    sessions.forEach(session => session.stop());
  };

  for (const provider of providers) {
    const connect = document.querySelector<HTMLButtonElement>(`#${provider.id}-connect-btn`);
    const disconnect = document.querySelector<HTMLButtonElement>(`#${provider.id}-disconnect-btn`);
    const status = document.getElementById(`${provider.id}-auth-status`);
    const restore = () => {
      if (connect) {
        connect.disabled = false;
        connect.textContent = `Connect ${provider.name}`;
      }
    };
    if (connect && provider.method) {
      const session = createDeviceAuthorization({
        provider: provider.name,
        token,
        startUrl: `/api/proxy/${provider.id}/auth/${provider.method}/start`,
        pollUrl: `/api/proxy/${provider.id}/auth/${provider.method}/poll`,
        onCode(code) {
          document.getElementById(`${provider.id}-auth-panel`)?.classList.remove("hidden");
          const url = document.getElementById(`${provider.id}-verify-url`);
          const userCode = document.getElementById(`${provider.id}-user-code`);
          if (url) url.textContent = code.verification_url || code.url || "";
          if (userCode) userCode.textContent = code.user_code;
          if (status) status.textContent = "Waiting for authorization…";
          connect.textContent = "Waiting…";
        },
        onConnected() {
          if (status) status.textContent = "Connected! Reloading…";
          window.location.reload();
        },
        onError(message) {
          if (status) status.textContent = message;
          showMessage(message, "error");
          restore();
        },
      });
      sessions.push(session);
      connect.onclick = () => {
        connect.disabled = true;
        connect.textContent = "Starting…";
        const credentials = Object.fromEntries(provider.credentialKeys.map(key => [
          key, document.querySelector<HTMLInputElement>(`#${key}`)?.value.trim() || "",
        ]));
        void session.start(Object.values(credentials).every(Boolean) ? credentials : undefined);
      };
    }

    if (disconnect) {
      disconnect.onclick = async () => {
        if (!confirm(`Disconnect ${provider.name}? This will remove your ${provider.name} ${provider.credentialLabel} from AnyList.`)) return;
        disconnect.disabled = true;
        disconnect.textContent = "Disconnecting…";
        try {
          const response = await fetch(`/api/proxy/${provider.id}/auth/disconnect`, {
            method: "DELETE", headers: { Authorization: `Bearer ${token}` }, signal: lifetime.signal,
          });
          if (lifetime.signal.aborted) return;
          if (!response.ok) {
            const data = await response.json();
            if (lifetime.signal.aborted) return;
            throw new Error(data.detail || `Failed to disconnect ${provider.name}`);
          }
          window.location.reload();
        } catch (cause) {
          if (lifetime.signal.aborted) return;
          showMessage(errorMessage(cause), "error");
          disconnect.disabled = false;
          disconnect.textContent = "Disconnect";
        }
      };
    }
  }
}

mount();
document.addEventListener("astro:after-swap", mount);
document.addEventListener("astro:before-swap", () => dispose?.());
