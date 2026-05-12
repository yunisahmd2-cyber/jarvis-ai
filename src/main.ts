import { createOrb, type OrbState } from "./orb";
import { backendWsUrl } from "./config";
import { createEarconPlayer } from "./earcons";
import { createMicVisualizer } from "./micVisualizer";
import {
  jarvisApi,
  type ActionResultPayload,
  type AssistantApiResponse,
} from "./services/jarvisApi";
import { isTauriRuntime, tauriDesktop } from "./services/tauriDesktop";
import { transcriptStore } from "./services/transcriptStore";
import { createAudioPlayer } from "./voice";
import { createSocket } from "./ws";
import "./style.css";

type State = "idle" | "listening" | "thinking" | "speaking";

let currentState: State = "idle";
let isMuted = false;
let isBusy = false;
let shouldFollowUp = false;
let sessionId = "";
let pendingThinkingTimer: number | null = null;
let pendingResponseTimeout: number | null = null;
let reminderPollInterval: number | null = null;
let pendingConfirmationResponse: AssistantApiResponse | null = null;
let wakeWordSummary = "";
let bootHideTimer: number | null = null;
let lastVoiceStartAt = 0;
let activeVoiceCycleId = 0;

const voiceStartDebounceMs = 450;
const thinkingTransitionDelayMs = 180;

const statusEl = document.getElementById("status-text") as HTMLElement | null;
const errorEl = document.getElementById("error-text") as HTMLElement | null;
const canvas = document.getElementById("orb-canvas") as HTMLCanvasElement | null;
const orbHitArea = document.getElementById("orb-hit-area") as HTMLButtonElement | null;
const confirmationOverlay = document.getElementById(
  "confirmation-overlay",
) as HTMLElement | null;
const confirmationText = document.getElementById(
  "confirmation-text",
) as HTMLElement | null;
const confirmationReason = document.getElementById(
  "confirmation-reason",
) as HTMLElement | null;
const btnConfirmApprove = document.getElementById(
  "btn-confirm-approve",
) as HTMLButtonElement | null;
const btnConfirmCancel = document.getElementById(
  "btn-confirm-cancel",
) as HTMLButtonElement | null;
const transcriptPanel = document.getElementById("transcript-panel") as HTMLElement | null;
const transcriptBody = document.getElementById("transcript-body") as HTMLElement | null;
const bootSequence = document.getElementById("boot-sequence") as HTMLElement | null;
const bootLinePrimary = document.getElementById("boot-line-primary") as HTMLElement | null;
const bootLineSecondary = document.getElementById("boot-line-secondary") as HTMLElement | null;
const missionPanel = document.getElementById("mission-panel") as HTMLElement | null;
const missionSubtitle = document.getElementById("mission-subtitle") as HTMLElement | null;
const missionBriefing = document.getElementById("mission-briefing") as HTMLElement | null;
const missionSystem = document.getElementById("mission-system") as HTMLElement | null;
const missionContext = document.getElementById("mission-context") as HTMLElement | null;
const missionSuggestions = document.getElementById("mission-suggestions") as HTMLElement | null;
const missionInspection = document.getElementById("mission-inspection") as HTMLElement | null;
const missionSpotify = document.getElementById("mission-spotify") as HTMLElement | null;
const missionWeather = document.getElementById("mission-weather") as HTMLElement | null;
const missionNews = document.getElementById("mission-news") as HTMLElement | null;
const missionCapabilities = document.getElementById("mission-capabilities") as HTMLElement | null;

if (!canvas) {
  throw new Error('Canvas with id "orb-canvas" not found');
}

if (!orbHitArea) {
  throw new Error('Button with id "orb-hit-area" not found');
}

function showError(msg: string) {
  if (!errorEl) return;
  errorEl.textContent = msg;
  errorEl.style.opacity = "1";
  transcriptStore.add("error", msg);
  earcons.play("error");
  setTimeout(() => {
    errorEl.style.opacity = "0";
  }, 4000);
}

function updateBootSequence(primary: string, secondary = "") {
  if (bootLinePrimary) {
    bootLinePrimary.textContent = primary;
  }
  if (bootLineSecondary) {
    bootLineSecondary.textContent = secondary;
  }
}

function hideBootSequence() {
  if (bootHideTimer !== null) {
    window.clearTimeout(bootHideTimer);
  }
  bootHideTimer = window.setTimeout(() => {
    bootSequence?.classList.add("hidden");
  }, 320);
}

function getStartupGreeting(): string {
  const hour = new Date().getHours();
  if (hour < 5) {
    return "Good evening. All systems are online. Standing by.";
  }
  if (hour < 12) {
    return "Good morning. All systems are online. Standing by.";
  }
  if (hour < 18) {
    return "Good afternoon. All systems are online. Standing by.";
  }
  return "Good evening. All systems are online. Standing by.";
}

function updateStatus(state: State, detail?: string) {
  if (!statusEl) return;

  const labels: Record<State, string> = {
    idle: "click orb to talk",
    listening: "listening...",
    thinking: detail || "thinking...",
    speaking: detail || "speaking...",
  };

  statusEl.textContent = labels[state];
  if (state === "idle" && wakeWordSummary) {
    statusEl.textContent = `${labels[state]} • ${wakeWordSummary}`;
  }
}

if (!isTauriRuntime()) {
  updateBootSequence(
    "desktop runtime required",
    "launch Jarvis from the Tauri desktop app",
  );
  if (statusEl) {
    statusEl.textContent = "desktop app required";
  }
  if (errorEl) {
    errorEl.textContent =
      "Browser runtime is disabled. Open Jarvis through the desktop app.";
    errorEl.style.opacity = "1";
  }
  orbHitArea.disabled = true;
  orbHitArea.setAttribute("aria-disabled", "true");
  throw new Error(
    "Jarvis frontend startup aborted: browser runtime is disabled; use the Tauri desktop app.",
  );
}

const orb = createOrb(canvas);
const socket = createSocket(backendWsUrl);
const audioPlayer = createAudioPlayer();
const earcons = createEarconPlayer(
  audioPlayer.getAnalyser().context as AudioContext,
);
const micVisualizer = createMicVisualizer(audioPlayer.getAnalyser().context as AudioContext);
orb.setAnalyser(audioPlayer.getAnalyser());

function usePlaybackAnalyser() {
  orb.setAnalyser(audioPlayer.getAnalyser());
}

async function useMicAnalyser() {
  const analyser = await micVisualizer.start();
  if (analyser && currentState === "listening") {
    orb.setAnalyser(analyser);
    return;
  }
  usePlaybackAnalyser();
}

function transition(newState: State, detail?: string) {
  if (currentState === newState && !detail) return;
  currentState = newState;
  orb.setState(newState as OrbState);
  updateStatus(newState, detail);
}

function clearThinkingTimer() {
  if (pendingThinkingTimer !== null) {
    window.clearTimeout(pendingThinkingTimer);
    pendingThinkingTimer = null;
  }
}

function clearResponseTimeout() {
  if (pendingResponseTimeout !== null) {
    window.clearTimeout(pendingResponseTimeout);
    pendingResponseTimeout = null;
  }
}

function armResponseTimeout() {
  clearResponseTimeout();
  pendingResponseTimeout = window.setTimeout(() => {
    isBusy = false;
    shouldFollowUp = false;
    transition("idle", "ready");
    showError("Jarvis did not finish responding. Please try again.");
  }, 30000);
}

function ensureAudioContext() {
  const ctx = audioPlayer.getAnalyser().context as AudioContext;
  if (ctx.state === "suspended") {
    ctx.resume().catch(() => {});
  }
}

function isVerifiedActionResult(result: ActionResultPayload | null | undefined) {
  return result?.status === "verified" && result?.success === true;
}

function isUnverifiedActionResult(result: ActionResultPayload | null | undefined) {
  return result?.status === "attempted_unverified";
}

function isFailedActionResult(result: ActionResultPayload | null | undefined) {
  return result?.status === "failed" || (result?.success === false && result?.attempted === false);
}

async function handleConfirmation(response: AssistantApiResponse) {
  const actionDescription =
    (response.action_preview?.description as string | undefined) || response.text;
  const actionName = (response.action_preview?.action as string | undefined) || "";
  const reason = actionName === "type_text" || actionName === "clipboard_write"
    ? "Authorization required. This affects active app or clipboard state."
    : "Authorization required. This action changes your desktop state or opens external content.";

  pendingConfirmationResponse = response;
  transition("thinking", "authorization pending...");
  transcriptStore.add("confirmation", actionDescription);
  earcons.play("confirmation");
  if (confirmationOverlay && confirmationText) {
    if (confirmationReason) {
      confirmationReason.textContent = reason;
    }
    confirmationText.textContent = actionDescription;
    confirmationOverlay.classList.remove("hidden");
    confirmationOverlay.setAttribute("aria-hidden", "false");
  }
}

async function submitConfirmation(approved: boolean) {
  const response = pendingConfirmationResponse;
  pendingConfirmationResponse = null;
  if (confirmationOverlay) {
    confirmationOverlay.classList.add("hidden");
    confirmationOverlay.setAttribute("aria-hidden", "true");
  }
  if (!response) {
    transition("idle");
    return;
  }
  try {
    const result = approved
      ? await jarvisApi.confirm(response.confirmation_id || "")
      : await jarvisApi.cancel(response.confirmation_id || "");

    const actionResult = (result.result?.result as ActionResultPayload | null | undefined) ?? undefined;
    const message =
      (actionResult?.message as string | undefined) ||
      (result.result?.message as string | undefined) ||
      (approved ? "Action confirmed." : "Action cancelled.");
    clearResponseTimeout();
    transcriptStore.add("system", message);
    if (approved && isVerifiedActionResult(actionResult)) {
      earcons.play("complete");
    } else if (approved && isFailedActionResult(actionResult)) {
      earcons.play("error");
      showError(message);
    }
    transition("idle", message);
  } catch (error) {
    clearResponseTimeout();
    transition("idle");
    showError(error instanceof Error ? error.message : "confirmation failed");
  }
}

function startBackendListening() {
  if (isMuted) return;
  if (!sessionId) {
    transition("idle", "session starting...");
    return;
  }
  if (isBusy || currentState === "listening" || currentState === "thinking") return;
  if (currentState === "speaking") return;
  const now = Date.now();
  if (now - lastVoiceStartAt < voiceStartDebounceMs) return;
  lastVoiceStartAt = now;
  const cycleId = ++activeVoiceCycleId;

  audioPlayer.stop();
  isBusy = true;
  shouldFollowUp = false;
  clearResponseTimeout();

  transition("listening");
  transcriptStore.add("system", "Listening");
  earcons.play("listening");
  void useMicAnalyser();

  clearThinkingTimer();
  pendingThinkingTimer = window.setTimeout(() => {
    if (currentState === "listening") {
      transition("thinking");
      armResponseTimeout();
    }
  }, thinkingTransitionDelayMs);

  void jarvisApi
    .voiceRespond(sessionId)
    .then(async (response) => {
      if (cycleId !== activeVoiceCycleId) return;
      await processAssistantResponse(response);
    })
    .catch((error) => {
      if (cycleId !== activeVoiceCycleId) return;
      clearThinkingTimer();
      clearResponseTimeout();
      micVisualizer.stop();
      usePlaybackAnalyser();
      isBusy = false;
      shouldFollowUp = false;
      transition("idle");
      showError(error instanceof Error ? error.message : "Voice request failed.");
    });
}

function startFollowUpListening() {
  if (isMuted) return;
  if (!sessionId) {
    transition("idle", "session starting...");
    return;
  }
  if (isBusy || currentState !== "idle") return;
  const now = Date.now();
  if (now - lastVoiceStartAt < voiceStartDebounceMs) return;
  lastVoiceStartAt = now;
  const cycleId = ++activeVoiceCycleId;

  isBusy = true;
  shouldFollowUp = false;
  clearResponseTimeout();

  transition("listening");
  transcriptStore.add("system", "Listening for follow-up");
  earcons.play("listening");
  void useMicAnalyser();

  clearThinkingTimer();
  pendingThinkingTimer = window.setTimeout(() => {
    if (currentState === "listening") {
      transition("thinking");
      armResponseTimeout();
    }
  }, thinkingTransitionDelayMs);

  void jarvisApi
    .voiceRespond(sessionId)
    .then(async (response) => {
      if (cycleId !== activeVoiceCycleId) return;
      await processAssistantResponse(response);
    })
    .catch((error) => {
      if (cycleId !== activeVoiceCycleId) return;
      clearThinkingTimer();
      clearResponseTimeout();
      micVisualizer.stop();
      usePlaybackAnalyser();
      isBusy = false;
      shouldFollowUp = false;
      transition("idle");
      showError(error instanceof Error ? error.message : "Follow-up voice request failed.");
    });
}

function handleOrbPress() {
  ensureAudioContext();
  clearThinkingTimer();
  clearResponseTimeout();

  if (currentState === "speaking" && sessionId) {
    activeVoiceCycleId += 1;
    audioPlayer.stop();
    micVisualizer.stop();
    usePlaybackAnalyser();
    shouldFollowUp = false;
    isBusy = false;
    jarvisApi.interrupt(sessionId).catch(() => {});
    transition("idle");
    window.setTimeout(() => {
      startBackendListening();
    }, 60);
    return;
  }

  startBackendListening();
}

audioPlayer.onFinished(() => {
  micVisualizer.stop();
  usePlaybackAnalyser();
  clearResponseTimeout();
  isBusy = false;
  if (shouldFollowUp) {
    transition("idle", "ready for follow-up");
    shouldFollowUp = false;
    return;
  }
  transition("idle");
});

socket.onMessage((msg) => {
  clearThinkingTimer();
  clearResponseTimeout();
  if (msg.event === "ack") {
    transition("thinking", (msg.text as string | undefined) || "working...");
    earcons.play("ack");
    const heard = msg.heard as string | undefined;
    if (heard) {
      transcriptStore.add("user", heard);
    }
    return;
  }
  const audioData =
    (msg.audio as string | undefined) ||
    (msg.audio_url as string | undefined);
  const text = msg.text as string | undefined;
  const followUp = Boolean(msg.follow_up);
  const confirmationRequired = Boolean(msg.confirmation_required);

  shouldFollowUp = followUp;

  if (confirmationRequired && text) {
    void handleConfirmation(msg as unknown as AssistantApiResponse);
    return;
  }

  if (audioData) {
    micVisualizer.stop();
    usePlaybackAnalyser();
    if (text) {
      transcriptStore.add("assistant", text);
    }
    if (!audioPlayer.isPlaying()) {
      transition("thinking", "voice ready...");
    }
    audioPlayer.enqueue(audioData, () => {
      transition("speaking", text);
    });
    return;
  }

  if (text) {
    transcriptStore.add("assistant", text);
    const source = (msg.metadata as Record<string, unknown> | undefined)?.source;
    const chainStatus = (msg.metadata as Record<string, unknown> | undefined)?.chain_status as string | undefined;
    const actionResult = ((msg.metadata as Record<string, unknown> | undefined)?.result as ActionResultPayload | undefined);
    if (!audioData && source === "action" && isVerifiedActionResult(actionResult)) {
      earcons.play("complete");
    } else if (!audioData && source === "action" && isFailedActionResult(actionResult)) {
      earcons.play("error");
    } else if (!audioData && source === "chain" && chainStatus?.startsWith("stopped")) {
      earcons.play("error");
    } else if (!audioData && (source === "chain" || source === "reminder")) {
      earcons.play("complete");
    }
    transition("idle", text);
    if (followUp) {
      transition("idle", "ready for follow-up");
      shouldFollowUp = false;
    }
    isBusy = false;
    return;
  }

  isBusy = false;
  micVisualizer.stop();
  usePlaybackAnalyser();
  transition("idle");
});

socket.onStatusChange((status) => {
  if (status === "connected") {
    updateBootSequence("backend link established", "session handshake ready");
    return;
  }

  if (status === "disconnected") {
    clearThinkingTimer();
    clearResponseTimeout();
    if (currentState === "thinking" || currentState === "listening") {
      micVisualizer.stop();
      usePlaybackAnalyser();
      isBusy = false;
      shouldFollowUp = false;
      transition("idle", "backend reconnecting");
      showError("Backend connection lost.");
    }
  }
});

orbHitArea.addEventListener("click", (event) => {
  event.stopPropagation();
  handleOrbPress();
});

document.addEventListener("keydown", (e) => {
  if (e.code === "Space") {
    e.preventDefault();
    if (e.repeat) return;
    if (currentState === "speaking") return;
    handleOrbPress();
  }
});

const btnMute = document.getElementById("btn-mute");
const btnMenu = document.getElementById("btn-menu");
const menuDropdown = document.getElementById("menu-dropdown");
const btnEndSession = document.getElementById("btn-end-session");
const btnMissionToggle = document.getElementById("btn-mission-toggle");
const btnMissionSpeak = document.getElementById("btn-mission-speak");
const btnMissionInspect = document.getElementById("btn-mission-inspect");
const btnMissionRefresh = document.getElementById("btn-mission-refresh");
const btnMissionClose = document.getElementById("btn-mission-close");
const btnTranscriptToggle = document.getElementById("btn-transcript-toggle");
const btnTranscriptClose = document.getElementById("btn-transcript-close");

btnConfirmApprove?.addEventListener("click", () => {
  void submitConfirmation(true);
});

btnConfirmCancel?.addEventListener("click", () => {
  void submitConfirmation(false);
});

btnMute?.addEventListener("click", (e) => {
  e.stopPropagation();
  isMuted = !isMuted;
  btnMute.classList.toggle("muted", isMuted);

  if (isMuted) {
    activeVoiceCycleId += 1;
    clearThinkingTimer();
    clearResponseTimeout();
    audioPlayer.stop();
    micVisualizer.stop();
    usePlaybackAnalyser();
    isBusy = false;
    shouldFollowUp = false;
    transition("idle", "muted");
  } else {
    transition("idle");
  }
});

btnMenu?.addEventListener("click", (e) => {
  e.stopPropagation();
  if (!menuDropdown) return;
  menuDropdown.style.display =
    menuDropdown.style.display === "none" ? "block" : "none";
});

document.addEventListener("click", () => {
  if (menuDropdown) menuDropdown.style.display = "none";
});

btnEndSession?.addEventListener("click", async (e) => {
  e.stopPropagation();
  if (menuDropdown) menuDropdown.style.display = "none";
  if (!sessionId) return;
  await jarvisApi.endSession(sessionId).catch(() => {});
  const session = await jarvisApi.startSession("frontend");
  sessionId = session.session_id;
  transcriptStore.clear();
  transition("idle", "new session ready");
});

btnTranscriptToggle?.addEventListener("click", (e) => {
  e.stopPropagation();
  if (menuDropdown) menuDropdown.style.display = "none";
  transcriptPanel?.classList.toggle("hidden");
});

btnTranscriptClose?.addEventListener("click", () => {
  transcriptPanel?.classList.add("hidden");
});

function setMissionText(element: HTMLElement | null, text: string) {
  if (!element) return;
  element.textContent = text;
}

function compactText(text: string, max = 380) {
  const cleaned = text.replace(/\s+/g, " ").trim();
  if (cleaned.length <= max) return cleaned;
  return `${cleaned.slice(0, max - 1).trim()}…`;
}

function formatSuggestionList(items: string[], fallback: string) {
  const deduped: string[] = [];
  const seen = new Set<string>();
  for (const item of items) {
    const cleaned = item.replace(/\s+/g, " ").trim();
    const key = cleaned.toLowerCase().replace(/\.$/, "");
    if (!cleaned || seen.has(key)) continue;
    seen.add(key);
    deduped.push(cleaned);
  }
  if (!deduped.length) return fallback;
  return deduped.slice(0, 5).map((item, index) => `${index + 1}. ${item.replace(/\.$/, "")}`).join("\n");
}

function missionStatusFlag(ok: boolean, label: string) {
  return ok ? `${label}: online` : `${label}: limited`;
}

async function processAssistantResponse(response: AssistantApiResponse) {
  clearThinkingTimer();
  clearResponseTimeout();
  micVisualizer.stop();
  usePlaybackAnalyser();

  const heard = response.metadata?.heard;
  if (typeof heard === "string" && heard.trim()) {
    transcriptStore.add("user", heard.trim());
  }

  if (response.confirmation_required && response.text) {
    await handleConfirmation(response);
    return;
  }

  if (response.text) {
    transcriptStore.add("assistant", response.text);
  }

  if (response.audio_url) {
    isBusy = true;
    shouldFollowUp = Boolean(response.follow_up);
    if (!audioPlayer.isPlaying()) {
      transition("thinking", "voice ready...");
    }
    audioPlayer.enqueue(response.audio_url, () => {
      transition("speaking", response.text || "speaking...");
    });
    return;
  }

  isBusy = false;
  shouldFollowUp = Boolean(response.follow_up);
  const actionResult = (response.metadata?.result as ActionResultPayload | undefined);
  const chainStatus = response.metadata?.chain_status as string | undefined;
  if (response.metadata?.source === "error") {
    showError(response.text || "Voice request failed.");
  } else if (response.metadata?.source === "action") {
    if (isVerifiedActionResult(actionResult)) {
      earcons.play("complete");
    } else if (isFailedActionResult(actionResult)) {
      earcons.play("error");
    }
  } else if (response.metadata?.source === "chain" && chainStatus?.startsWith("stopped")) {
    earcons.play("error");
  }
  transition("idle", response.text || "ready");
  if (shouldFollowUp) {
    transition("idle", "ready for follow-up");
    shouldFollowUp = false;
  }
}

async function refreshMissionControl() {
  setMissionText(missionBriefing, "Refreshing briefing…");
  setMissionText(missionSystem, "Refreshing system report…");
  if (missionSubtitle) {
    missionSubtitle.textContent = "Refreshing context systems…";
  }
  try {
    const [briefing, systemReport, capabilities, modeProfile, browser, browserAwareness, browserPage, appIntel, contextBrief, weather, news] = await Promise.all([
      jarvisApi.systemBriefing(),
      jarvisApi.systemReport(),
      jarvisApi.systemCapabilities(),
      jarvisApi.systemModeProfile(),
      jarvisApi.browserContext(),
      jarvisApi.browserAwareness(),
      jarvisApi.browserPageSummary(),
      jarvisApi.activeAppIntelligence(),
      jarvisApi.systemContext(),
      jarvisApi.weather(),
      jarvisApi.news(),
    ]);

    setMissionText(missionBriefing, briefing.summary);
    setMissionText(missionSystem, systemReport.summary);
    if (missionSubtitle) {
      const timestamp = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      missionSubtitle.textContent = `Updated ${timestamp} • ${modeProfile.mode.toUpperCase()} mode`;
    }

    const activeAppMessage = (appIntel.active_app?.message as string | undefined)
      || (systemReport.active_app?.message as string | undefined)
      || "Active app context unavailable.";
    const browserLine = browserAwareness.ok
      ? `Current page: ${browserAwareness.title || browserAwareness.url || "available"}`
      : `Current page: ${browserAwareness.message}`;
    const contextLines = [
      missionStatusFlag(Boolean(appIntel.ok), "App context"),
      missionStatusFlag(Boolean(browserAwareness.ok), "Page context"),
      activeAppMessage,
      browserLine,
      browser.ok && browser.url ? `URL: ${browser.url}` : null,
      browserAwareness.domain ? `Domain: ${browserAwareness.domain}` : null,
      browserPage.summary ? `Page summary: ${compactText(browserPage.summary, 320)}` : null,
      `Context brief: ${compactText(contextBrief.summary, 320)}`,
    ].filter(Boolean);
    setMissionText(missionContext, contextLines.join("\n"));
    setMissionText(
      missionSuggestions,
      formatSuggestionList(
        (appIntel.suggestions && appIntel.suggestions.length > 0
          ? appIntel.suggestions
          : systemReport.suggestions && systemReport.suggestions.length > 0
            ? systemReport.suggestions
            : []),
        "No specific suggestions are available right now.",
      ),
    );

    const spotify = systemReport.spotify as Record<string, unknown>;
    const spotifyText = spotify.running
      ? `${spotify.player_state || "running"}\n${spotify.track || "Unknown track"}\n${spotify.artist || "Unknown artist"}`
      : String((spotify.message as string | undefined) || "Spotify is not running.");
    setMissionText(missionSpotify, spotifyText);

    setMissionText(missionWeather, compactText(weather.summary, 340));
    setMissionText(missionNews, compactText(news.summary, 340));
    const modeFeatureLines = Object.entries(modeProfile.features || {})
      .map(([key, value]) => `${key.replace(/_/g, " ")}: ${value}`)
      .join("\n");
    setMissionText(
      missionCapabilities,
      `${capabilities.summary}\n\nMode profile: ${modeProfile.summary}\n${modeFeatureLines}`,
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : "Mission Control unavailable";
    if (missionSubtitle) {
      missionSubtitle.textContent = "Context systems unavailable";
    }
    setMissionText(missionBriefing, message);
    setMissionText(missionSystem, message);
    setMissionText(missionContext, message);
    setMissionText(missionSuggestions, message);
    setMissionText(missionSpotify, message);
    setMissionText(missionWeather, message);
    setMissionText(missionNews, message);
    setMissionText(missionCapabilities, message);
  }
}

async function inspectCurrentContext() {
  if (!sessionId) {
    setMissionText(missionInspection, "Inspection is unavailable until the session is ready.");
    return;
  }
  if (!isTauriRuntime()) {
    setMissionText(missionInspection, "Screen inspection is available only inside the Tauri desktop app.");
    return;
  }

  setMissionText(missionInspection, "Capturing current screen and page context…");
  try {
    const screenshot = await tauriDesktop.captureScreenshot();
    const [vision, operator] = await Promise.all([
      jarvisApi.analyzeVision(sessionId, screenshot.data_url, "Inspect the current screen for a Jarvis operator briefing."),
      jarvisApi.systemOperatorBriefing(),
    ]);

    const analysisLevel = String(vision.metadata?.analysis_level || "metadata");
    const ocrState = String(vision.metadata?.ocr_state || "disabled");
    const metadataActionsRaw = vision.metadata?.suggested_next_actions;
    const metadataActions = Array.isArray(metadataActionsRaw)
      ? metadataActionsRaw.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
      : [];
    const ocrText = (vision.ocr_text || "").trim();
    const ocrExcerpt = ocrText ? `${ocrText.slice(0, 420)}${ocrText.length > 420 ? "…" : ""}` : null;

    const suggestionPool = [...operator.suggestions];
    if (ocrText) {
      suggestionPool.push(
        "Search based on this screen text.",
        "Compare this with another source.",
      );
    } else if (ocrState === "unavailable") {
      suggestionPool.push("Install and enable OCR for richer screen reading.");
    } else if (analysisLevel === "metadata") {
      suggestionPool.push("Use page context or inspect again with OCR enabled.");
    }
    if (metadataActions.length > 0) {
      suggestionPool.push(...metadataActions);
    }

    const combined = [
      `Operator brief: ${compactText(operator.summary, 420)}`,
      `Vision summary: ${compactText(vision.summary, 420)}`,
      `Analysis level: ${analysisLevel}`,
      `OCR state: ${ocrState}`,
      ocrExcerpt ? `Detected screen text:\n${ocrExcerpt}` : null,
      (operator.page_summary?.summary as string | undefined) || null,
      suggestionPool.length > 0 ? `Suggested actions:\n${formatSuggestionList(suggestionPool, "No suggestions")}` : null,
      screenshot.width && screenshot.height ? `Captured frame: ${screenshot.width}x${screenshot.height}.` : null,
    ]
      .filter(Boolean)
      .join("\n\n");

    setMissionText(missionInspection, combined);
    setMissionText(
      missionSuggestions,
      formatSuggestionList(suggestionPool, "No specific suggestions are available right now."),
    );
    transcriptStore.add("vision", "Operator briefing updated");
  } catch (error) {
    setMissionText(
      missionInspection,
      error instanceof Error ? error.message : "Screen inspection failed.",
    );
  }
}

async function speakMissionBriefing() {
  if (!sessionId) {
    setMissionText(missionInspection, "Briefing is unavailable until the session is ready.");
    return;
  }
  if (currentState === "listening" || currentState === "thinking") {
    return;
  }

  audioPlayer.stop();
  isBusy = true;
  shouldFollowUp = false;
  transition("thinking", "preparing operator briefing...");

  try {
    const response = await jarvisApi.sendText("operator briefing", sessionId, true);
    await processAssistantResponse(response);
  } catch (error) {
    isBusy = false;
    shouldFollowUp = false;
    transition("idle");
    showError(error instanceof Error ? error.message : "Mission briefing failed.");
  }
}

btnMissionToggle?.addEventListener("click", (e) => {
  e.stopPropagation();
  if (menuDropdown) menuDropdown.style.display = "none";
  const opening = missionPanel?.classList.contains("hidden") ?? false;
  missionPanel?.classList.toggle("hidden");
  if (opening) {
    void refreshMissionControl();
  }
});

btnMissionRefresh?.addEventListener("click", () => {
  void refreshMissionControl();
});

btnMissionInspect?.addEventListener("click", () => {
  void inspectCurrentContext();
});

btnMissionSpeak?.addEventListener("click", () => {
  void speakMissionBriefing();
});

btnMissionClose?.addEventListener("click", () => {
  missionPanel?.classList.add("hidden");
});

function renderTranscript() {
  if (!transcriptBody) return;
  const entries = transcriptStore.getEntries();
  transcriptBody.innerHTML = entries
    .map(
      (entry) => `
        <div class="transcript-entry">
          <div class="role">${entry.role}</div>
          <div class="text">${entry.text}</div>
          <div class="meta">${new Date(entry.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}${entry.meta ? ` · ${entry.meta}` : ""}</div>
        </div>
      `,
    )
    .join("");
  transcriptBody.scrollTop = transcriptBody.scrollHeight;
}

transcriptStore.subscribe(() => {
  renderTranscript();
});

function startReminderPolling() {
  if (reminderPollInterval !== null) {
    window.clearInterval(reminderPollInterval);
  }
  reminderPollInterval = window.setInterval(async () => {
    try {
      const reminders = await jarvisApi.listDueReminders();
      for (const reminder of reminders) {
        transcriptStore.add("system", `Reminder: ${reminder.title}`);
        transition("idle", `reminder: ${reminder.title}`);
        earcons.play("reminder");
        await jarvisApi.completeReminder(reminder.id);
        earcons.play("complete");
      }
    } catch {
      // Keep reminder polling best-effort and quiet.
    }
  }, 20000);
}

async function bootstrap() {
  try {
    updateBootSequence("checking backend health…", "warming local systems");
    await jarvisApi.health();
    updateBootSequence("voice session online", "initializing session context");
    const session = await jarvisApi.startSession("frontend");
    sessionId = session.session_id;
    startReminderPolling();
    updateBootSequence("synchronizing wake word policy", "loading mission control");
    const wakeWord = await jarvisApi.wakeWordStatus();
    wakeWordSummary =
      wakeWord.power_mode === "basic"
        ? "wake word paused"
        : wakeWord.effective_enabled
          ? "wake word ready"
          : "wake word off";
    transcriptStore.add("system", wakeWord.reason);
    await refreshMissionControl();
    const startupGreeting = getStartupGreeting();
    updateBootSequence("systems online", startupGreeting);
    transition("idle", "systems online");
    hideBootSequence();
  } catch (error) {
    showError(error instanceof Error ? error.message : "backend unavailable");
    updateBootSequence("startup degraded", "backend unavailable");
  }
}

void bootstrap();
transition("idle");
