export type TranscriptRole =
  | "user"
  | "assistant"
  | "system"
  | "error"
  | "confirmation"
  | "vision";

export interface TranscriptEntry {
  id: string;
  role: TranscriptRole;
  text: string;
  timestamp: string;
  meta?: string;
}

type TranscriptListener = (entries: TranscriptEntry[]) => void;

class TranscriptStore {
  private entries: TranscriptEntry[] = [];
  private listeners = new Set<TranscriptListener>();

  subscribe(listener: TranscriptListener): () => void {
    this.listeners.add(listener);
    listener(this.entries);
    return () => this.listeners.delete(listener);
  }

  getEntries(): TranscriptEntry[] {
    return [...this.entries];
  }

  add(role: TranscriptRole, text: string, meta?: string) {
    this.entries = [
      ...this.entries,
      {
        id: crypto.randomUUID(),
        role,
        text,
        timestamp: new Date().toISOString(),
        meta,
      },
    ];
    this.emit();
  }

  clear() {
    this.entries = [];
    this.emit();
  }

  private emit() {
    for (const listener of this.listeners) {
      listener(this.getEntries());
    }
  }
}

export const transcriptStore = new TranscriptStore();
