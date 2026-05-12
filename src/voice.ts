export interface AudioPlayer {
  enqueue(src: string, onStarted?: () => void): void;
  stop(): void;
  onFinished(cb: () => void): void;
  getAnalyser(): AnalyserNode;
  isPlaying(): boolean;
}

export function createAudioPlayer(): AudioPlayer {
  const audio = new Audio();
  audio.preload = "auto";
  audio.crossOrigin = "anonymous";

  const audioCtx = new AudioContext();
  const source = audioCtx.createMediaElementSource(audio);
  const analyser = audioCtx.createAnalyser();

  analyser.fftSize = 128;

  source.connect(analyser);
  analyser.connect(audioCtx.destination);

  let finishedCb: (() => void) | null = null;
  let playbackToken = 0;

  interface QueueItem {
    src: string;
    onStarted?: () => void;
  }
  const queue: QueueItem[] = [];
  let isPlaying = false;

  function start(token: number, cb?: () => void) {
    if (token !== playbackToken) return;
    cb?.();
  }

  function playNext() {
    if (queue.length === 0) {
      isPlaying = false;
      finishedCb?.();
      return;
    }
    isPlaying = true;
    const item = queue.shift()!;
    const token = playbackToken + 1;
    playbackToken = token;
    let started = false;
    audio.pause();
    audio.currentTime = 0;
    audio.onplaying = () => {
      if (started) return;
      started = true;
      start(token, item.onStarted);
    };
    audio.onended = () => {
      if (token !== playbackToken) return;
      playNext();
    };
    audio.onerror = () => {
      console.error("[audio] failed to load/play");
      if (token !== playbackToken) return;
      playNext();
    };
    audio.src = item.src;
    audio.load();

    if (audioCtx.state === "suspended") {
      audioCtx.resume().catch(() => {});
    }

    audio
      .play()
      .then(() => {
        if (started) return;
        started = true;
        start(token, item.onStarted);
      })
      .catch((err) => {
        console.error("[audio] play failed", err);
        if (token !== playbackToken) return;
        playNext();
      });
  }

  audio.onended = () => {};
  audio.onerror = () => {};

  return {
    enqueue(src: string, onStarted?: () => void) {
      queue.push({ src, onStarted });
      if (!isPlaying) {
        playNext();
      }
    },

    stop() {
      playbackToken += 1;
      queue.length = 0;
      isPlaying = false;
      audio.pause();
      audio.currentTime = 0;
      audio.removeAttribute("src");
      audio.load();
    },

    onFinished(cb: () => void) {
      finishedCb = cb;
    },

    getAnalyser() {
      return analyser;
    },

    isPlaying() {
      return isPlaying;
    },
  };
}
