type TauriInvoke = (command: string, args?: Record<string, unknown>) => Promise<unknown>;

export interface ScreenshotCaptureResult {
  data_url: string;
  mime_type: string;
  width?: number | null;
  height?: number | null;
  captured_at_ms: number;
}

declare global {
  interface Window {
    __TAURI__?: {
      core?: {
        invoke?: TauriInvoke;
      };
    };
  }
}

function getInvoke(): TauriInvoke | null {
  return window.__TAURI__?.core?.invoke ?? null;
}

export function isTauriRuntime(): boolean {
  return Boolean(getInvoke());
}

export const tauriDesktop = {
  async captureScreenshot(): Promise<ScreenshotCaptureResult> {
    const invoke = getInvoke();
    if (!invoke) {
      throw new Error("Native screenshot capture is available only inside the Tauri desktop app.");
    }

    const result = await invoke("capture_screenshot");
    return result as ScreenshotCaptureResult;
  },
};
