import { backendBaseUrl } from "../config";

export interface SessionInfo {
  session_id: string;
  status: "active" | "ended";
  created_at: string;
  ended_at?: string | null;
}

export interface AssistantApiResponse {
  session_id: string;
  text: string;
  audio_url?: string | null;
  follow_up: boolean;
  confirmation_required: boolean;
  confirmation_id?: string | null;
  action_preview?: Record<string, unknown> | null;
  memory_updated: boolean;
  metadata: Record<string, unknown>;
}

export interface ActionResultPayload {
  ok?: boolean;
  success?: boolean;
  verified?: boolean;
  attempted?: boolean;
  status?: "verified" | "attempted_unverified" | "failed" | string;
  message?: string;
  [key: string]: unknown;
}

export interface ConfirmationApiResponse {
  confirmation_id: string;
  status: "confirmed" | "confirmed_unverified" | "confirmed_failed" | "cancelled" | "not_found";
  result?: {
    ok?: boolean;
    status?: string;
    message?: string;
    result?: ActionResultPayload | null;
  } | null;
}

export interface VisionAnalyzeResponse {
  session_id?: string | null;
  summary: string;
  ocr_text?: string | null;
  metadata: Record<string, unknown>;
}

export interface SpotifyStatusResponse {
  enabled: boolean;
  available: boolean;
  running: boolean;
  player_state: string;
  track?: string | null;
  artist?: string | null;
  album?: string | null;
  position_seconds?: number | null;
  message: string;
}

export interface SystemReportResponse {
  ok: boolean;
  power_mode: "basic" | "advanced";
  wake_word: Record<string, unknown>;
  active_app: Record<string, unknown>;
  browser: Record<string, unknown>;
  spotify: Record<string, unknown>;
  suggestions?: string[];
  summary: string;
}

export interface SystemCapabilitiesResponse {
  ok: boolean;
  platform: string;
  allowed_apps: string[];
  allowed_folders: string[];
  capabilities: Record<string, unknown>;
  wake_word: Record<string, unknown>;
  summary: string;
}

export interface SystemBriefingResponse {
  ok: boolean;
  summary: string;
  power_mode: "basic" | "advanced";
  active_app: Record<string, unknown>;
  weather: Record<string, unknown>;
  news: Record<string, unknown>;
  reminders: string;
  spotify: Record<string, unknown>;
}

export interface OperatorBriefingResponse {
  ok: boolean;
  summary: string;
  power_mode: "basic" | "advanced";
  active_app: Record<string, unknown>;
  browser: Record<string, unknown>;
  page_summary: Record<string, unknown>;
  weather: Record<string, unknown>;
  news: Record<string, unknown>;
  reminders: string;
  spotify: Record<string, unknown>;
  suggestions: string[];
}

export interface ReminderInfo {
  id: string;
  title: string;
  due_at: string;
  created_at: string;
  completed_at?: string | null;
  session_id?: string | null;
}

export interface WeatherResponse {
  ok: boolean;
  summary: string;
  raw?: Record<string, unknown>;
}

export interface NewsResponse {
  ok: boolean;
  summary: string;
  headlines?: string[];
}

export interface BrowserContextResponse {
  ok: boolean;
  app?: string | null;
  url?: string | null;
  title?: string | null;
  message: string;
}

export interface BrowserAwarenessResponse extends BrowserContextResponse {
  domain?: string | null;
}

export interface BrowserPageSummaryResponse {
  ok: boolean;
  summary: string;
  mode?: "basic" | "advanced";
  context?: Record<string, unknown>;
}

export interface ActiveAppIntelligenceResponse {
  ok: boolean;
  app?: string | null;
  active_app: Record<string, unknown>;
  browser: Record<string, unknown>;
  spotify: Record<string, unknown>;
  suggestions: string[];
  summary: string;
}

export interface ContextBriefResponse {
  ok: boolean;
  active_app: Record<string, unknown>;
  browser: Record<string, unknown>;
  page_summary?: Record<string, unknown> | null;
  spotify: Record<string, unknown>;
  suggestions: string[];
  summary: string;
}

export interface ModeProfileResponse {
  mode: "basic" | "advanced";
  summary: string;
  features: Record<string, string>;
}

export interface WakeWordStatus {
  wake_word: string;
  desired_enabled: boolean;
  effective_enabled: boolean;
  power_mode: "basic" | "advanced";
  listener_active: boolean;
  load_paused: boolean;
  reason: string;
}

export interface HealthInfo {
  ok: boolean;
  service: string;
  time: string;
  version: string;
  model: string;
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${backendBaseUrl}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!response.ok) {
    const text = await response.text();
    if (text) {
      let detail: string | undefined;
      try {
        const parsed = JSON.parse(text) as { detail?: string };
        detail = parsed.detail;
      } catch {
        // Fall back to the raw response body.
      }
      if (detail) {
        throw new Error(detail);
      }
      throw new Error(text || `Request failed: ${response.status}`);
    }
    throw new Error(`Request failed: ${response.status}`);
  }

  return (await response.json()) as T;
}

export const jarvisApi = {
  async health() {
    return requestJson<HealthInfo>("/health");
  },
  async startSession(sessionName = "frontend") {
    return requestJson<SessionInfo>("/voice/session/start", {
      method: "POST",
      body: JSON.stringify({ session_name: sessionName }),
    });
  },
  async endSession(sessionId: string) {
    return requestJson<SessionInfo>("/voice/session/end", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId }),
    });
  },
  async voiceRespond(sessionId: string) {
    return requestJson<AssistantApiResponse>("/voice/respond", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId }),
    });
  },
  async sendText(text: string, sessionId: string, includeAudio = false) {
    return requestJson<AssistantApiResponse>("/assistant/respond", {
      method: "POST",
      body: JSON.stringify({
        text,
        session_id: sessionId,
        include_audio: includeAudio,
      }),
    });
  },
  async sendTextWithScreen(
    text: string,
    sessionId: string,
    screenshotBase64: string,
    includeAudio = false,
  ) {
    return requestJson<AssistantApiResponse>("/assistant/respond", {
      method: "POST",
      body: JSON.stringify({
        text,
        session_id: sessionId,
        include_audio: includeAudio,
        include_screen_context: true,
        screenshot_base64: screenshotBase64,
      }),
    });
  },
  async interrupt(sessionId: string) {
    return requestJson<{ interrupted: boolean; session_id?: string | null }>(
      "/voice/interrupt",
      {
        method: "POST",
        body: JSON.stringify({ session_id: sessionId }),
      },
    );
  },
  async confirm(confirmationId: string) {
    return requestJson<ConfirmationApiResponse>(
      "/assistant/confirm",
      {
        method: "POST",
        body: JSON.stringify({ confirmation_id: confirmationId }),
      },
    );
  },
  async cancel(confirmationId: string) {
    return requestJson<ConfirmationApiResponse>(
      "/assistant/cancel",
      {
        method: "POST",
        body: JSON.stringify({ confirmation_id: confirmationId }),
      },
    );
  },
  async analyzeVision(sessionId: string, screenshotBase64: string, prompt?: string) {
    return requestJson<VisionAnalyzeResponse>("/vision/analyze", {
      method: "POST",
      body: JSON.stringify({
        session_id: sessionId,
        screenshot_base64: screenshotBase64,
        prompt,
      }),
    });
  },
  async spotifyStatus() {
    return requestJson<SpotifyStatusResponse>("/integrations/spotify/status");
  },
  async activeApp() {
    return requestJson<{ ok: boolean; app?: string | null; message: string }>(
      "/integrations/system/active-app",
    );
  },
  async browserContext() {
    return requestJson<BrowserContextResponse>("/integrations/browser/context");
  },
  async browserAwareness() {
    return requestJson<BrowserAwarenessResponse>("/integrations/browser/awareness");
  },
  async browserPageSummary() {
    return requestJson<BrowserPageSummaryResponse>("/integrations/browser/page-summary");
  },
  async weather(place = "Muscat") {
    return requestJson<WeatherResponse>(`/integrations/weather?place=${encodeURIComponent(place)}`);
  },
  async news(topic = "technology") {
    return requestJson<NewsResponse>(`/integrations/news?topic=${encodeURIComponent(topic)}`);
  },
  async systemReport() {
    return requestJson<SystemReportResponse>("/integrations/system/report");
  },
  async systemContext() {
    return requestJson<ContextBriefResponse>("/integrations/system/context");
  },
  async activeAppIntelligence() {
    return requestJson<ActiveAppIntelligenceResponse>("/integrations/system/active-app/intelligence");
  },
  async systemCapabilities() {
    return requestJson<SystemCapabilitiesResponse>("/integrations/system/capabilities");
  },
  async systemModeProfile() {
    return requestJson<ModeProfileResponse>("/integrations/system/mode-profile");
  },
  async systemBriefing() {
    return requestJson<SystemBriefingResponse>("/integrations/system/briefing");
  },
  async systemOperatorBriefing() {
    return requestJson<OperatorBriefingResponse>("/integrations/system/operator-briefing");
  },
  async createReminder(title: string, dueAt: string, sessionId?: string) {
    return requestJson<ReminderInfo>("/reminders", {
      method: "POST",
      body: JSON.stringify({
        title,
        due_at: dueAt,
        session_id: sessionId,
      }),
    });
  },
  async listDueReminders() {
    return requestJson<ReminderInfo[]>("/reminders/due");
  },
  async completeReminder(reminderId: string) {
    return requestJson<ReminderInfo | null>(`/reminders/${reminderId}/complete`, {
      method: "POST",
    });
  },
  async wakeWordStatus() {
    return requestJson<WakeWordStatus>("/voice/wake-word/status");
  },
  async toggleWakeWord(enabled: boolean) {
    return requestJson<WakeWordStatus>("/voice/wake-word/toggle", {
      method: "POST",
      body: JSON.stringify({ enabled }),
    });
  },
};
