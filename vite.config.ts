import { defineConfig } from "vite";

export default defineConfig({
  clearScreen: false,
  build: {
    // Three.js powers the orb and is isolated below as a known vendor chunk.
    chunkSizeWarningLimit: 550,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("/node_modules/three/")) {
            return "three";
          }
          return undefined;
        },
      },
    },
  },
  server: {
    host: "127.0.0.1",
    open: false,
    port: 5173,
    strictPort: true,
  },
});
