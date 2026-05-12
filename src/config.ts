const defaultBackendUrl = "http://127.0.0.1:8000";

export const backendBaseUrl =
  (import.meta.env.VITE_BACKEND_BASE_URL as string | undefined)?.trim() ||
  defaultBackendUrl;

export const backendWsUrl =
  (import.meta.env.VITE_BACKEND_WS_URL as string | undefined)?.trim() ||
  backendBaseUrl.replace(/^http/, "ws") + "/ws";
