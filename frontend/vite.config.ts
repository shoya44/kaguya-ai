import { defineConfig } from 'vite';

// Milestone 2: talk to the FastAPI dev server directly (no reverse proxy yet).
export default defineConfig({
  server: {
    host: '127.0.0.1',
    port: 5173,
  },
});
