import { api } from "./api";

/** Whether the signed-in user has a Stremio or Nuvio connection for library sync. */
export async function hasStreamingLibraryConnection(token?: string): Promise<boolean> {
  if (!token) return false;
  try {
    const connections = await api.auth.getConnections(token);
    return connections.some(connection => connection.type === "stremio" || connection.type === "nuvio");
  } catch {
    // Fail closed if connection state cannot be confirmed.
    return false;
  }
}
