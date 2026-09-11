// Tauri-only glue. iPhone/Safari loads the same main.ts/avatar.ts/style.css
// but this module's isTauri() simply returns false there, so the shared UI
// code works unchanged in a plain browser tab (spec section 3: "the web
// code is shared with iPhone; only Tauri-specific handling is separated").
//
// Backend readiness is no longer signaled by a Tauri event: main.ts polls
// /health directly with retries instead. This avoids the listener/emit
// race that a one-shot event has (a fast backend can finish before the
// frontend has registered its listener).
import { isTauri as coreIsTauri } from '@tauri-apps/api/core';

export function isTauri(): boolean {
  return coreIsTauri();
}
