import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `npm run dev` on :5173 forwards API calls to the planner on :8000.
const planner = "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: { proxy: Object.fromEntries(["/chat", "/plans", "/users", "/stt"].map((p) => [p, planner])) },
});
