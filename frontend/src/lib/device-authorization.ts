export interface DeviceCode {
  user_code: string;
  verification_url?: string;
  url?: string;
  interval: number;
  expires_in: number;
}

interface AuthorizationOptions {
  provider: string;
  token: string;
  startUrl: string;
  pollUrl: string;
  onCode: (code: DeviceCode) => void;
  onConnected: () => void;
  onError: (message: string) => void;
}

function wait(delay: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const finish = () => {
      signal.removeEventListener("abort", cancel);
      resolve();
    };
    const timer = setTimeout(finish, delay);
    const cancel = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", cancel);
      reject(new Error("Authorization cancelled"));
    };
    signal.addEventListener("abort", cancel, { once: true });
    if (signal.aborted) cancel();
  });
}

/** Own one attempt, including its requests and deadline, until stopped or replaced. */
export function createDeviceAuthorization(options: AuthorizationOptions) {
  let current: AbortController | undefined;
  let stopped = false;

  return {
    async start(credentials?: Record<string, string>) {
      if (stopped) return;
      current?.abort();
      const controller = (current = new AbortController());
      const { signal } = controller;
      const headers = { Authorization: `Bearer ${options.token}` };

      async function request(url: string, body?: Record<string, string>) {
        const response = await fetch(url, {
          method: body ? "PATCH" : "POST",
          headers: body ? { ...headers, "Content-Type": "application/json" } : headers,
          body: body ? JSON.stringify(body) : undefined,
          signal,
        });
        const data = await response.json();
        if (signal.aborted) throw new Error("Authorization cancelled");
        if (!response.ok) throw new Error(data.detail || `${options.provider} authorization failed`);
        return data;
      }

      try {
        if (credentials) await request("/api/proxy/auth/settings", credentials);
        const code: DeviceCode = await request(options.startUrl);
        const expiresIn = Number(code.expires_in);
        if (!Number.isFinite(expiresIn) || expiresIn <= 0) {
          throw new Error(`${options.provider} returned an invalid authorization expiry`);
        }
        const interval = Math.max(1, Number(code.interval) || 5) * 1000;
        const deadline = Date.now() + expiresIn * 1000;
        options.onCode(code);

        while (!signal.aborted) {
          await wait(Math.min(interval, Math.max(0, deadline - Date.now())), signal);
          if (Date.now() >= deadline) throw new Error("Authorization timed out. Please try again.");
          // Network outages are retryable until expiry; a rejected HTTP response is terminal.
          let response: Response;
          try {
            response = await fetch(options.pollUrl, { method: "POST", headers, signal });
          } catch (cause) {
            if (signal.aborted) throw cause;
            continue;
          }
          const data = await response.json();
          if (signal.aborted) return;
          if (!response.ok) throw new Error(data.detail || `${options.provider} authorization failed`);
          if (data.status === "connected") {
            options.onConnected();
            return;
          }
        }
      } catch (cause) {
        if (!signal.aborted) options.onError(cause instanceof Error ? cause.message : "Authorization failed");
      }
    },
    stop() {
      stopped = true;
      current?.abort();
    },
  };
}
