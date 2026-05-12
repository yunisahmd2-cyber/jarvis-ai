export type EarconType =
  | "listening"
  | "ack"
  | "confirmation"
  | "complete"
  | "error"
  | "reminder";

export interface EarconPlayer {
  play(type: EarconType): void;
}

export function createEarconPlayer(audioContext: AudioContext): EarconPlayer {
  function pulse(
    frequency: number,
    duration: number,
    gainValue: number,
    kind: OscillatorType = "sine",
    delay = 0,
  ) {
    const now = audioContext.currentTime + delay;
    const oscillator = audioContext.createOscillator();
    const gain = audioContext.createGain();
    oscillator.type = kind;
    oscillator.frequency.setValueAtTime(frequency, now);
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(gainValue, now + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + duration);
    oscillator.connect(gain);
    gain.connect(audioContext.destination);
    oscillator.start(now);
    oscillator.stop(now + duration + 0.03);
  }

  return {
    play(type) {
      if (audioContext.state === "suspended") {
        audioContext.resume().catch(() => {});
      }

      if (type === "listening") {
        pulse(640, 0.08, 0.018, "sine");
        pulse(820, 0.06, 0.014, "sine", 0.05);
        return;
      }
      if (type === "ack") {
        pulse(520, 0.07, 0.014, "triangle");
        return;
      }
      if (type === "confirmation") {
        pulse(420, 0.09, 0.016, "triangle");
        pulse(620, 0.08, 0.014, "triangle", 0.08);
        return;
      }
      if (type === "reminder") {
        pulse(740, 0.08, 0.02, "sine");
        pulse(880, 0.07, 0.016, "sine", 0.09);
        return;
      }
      if (type === "complete") {
        pulse(560, 0.06, 0.014, "triangle");
        pulse(760, 0.06, 0.012, "triangle", 0.06);
        return;
      }
      pulse(240, 0.12, 0.016, "sawtooth");
    },
  };
}
