import type { Plugin } from "vite";

/** Keep development and preview assets fresh. Production builds are unchanged. */
export function developmentFreshness(options?: {
  /** Absolute package directories to watch, including mounted shared builds. */
  watchDirectories?: readonly string[];
}): Plugin;
