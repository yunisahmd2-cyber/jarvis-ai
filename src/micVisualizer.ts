export interface MicVisualizer {
  start(): Promise<AnalyserNode | null>;
  stop(): void;
  isActive(): boolean;
}

export function createMicVisualizer(audioCtx: AudioContext): MicVisualizer {
  let stream: MediaStream | null = null;
  let source: MediaStreamAudioSourceNode | null = null;
  let analyser: AnalyserNode | null = null;
  let pending: Promise<AnalyserNode | null> | null = null;

  async function start() {
    if (analyser) {
      return analyser;
    }
    if (pending) {
      return pending;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      return null;
    }

    pending = navigator.mediaDevices
      .getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      })
      .then((mediaStream) => {
        stream = mediaStream;
        if (audioCtx.state === "suspended") {
          audioCtx.resume().catch(() => {});
        }
        source = audioCtx.createMediaStreamSource(mediaStream);
        analyser = audioCtx.createAnalyser();
        analyser.fftSize = 128;
        source.connect(analyser);
        return analyser;
      })
      .catch(() => null)
      .finally(() => {
        pending = null;
      });

    return pending;
  }

  function stop() {
    source?.disconnect();
    stream?.getTracks().forEach((track) => track.stop());
    source = null;
    stream = null;
    analyser = null;
  }

  return {
    start,
    stop,
    isActive() {
      return Boolean(stream && analyser);
    },
  };
}
