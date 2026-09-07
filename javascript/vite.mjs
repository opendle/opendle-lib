import { randomUUID } from "node:crypto";
import { watch } from "node:fs";
import { isAbsolute } from "node:path";

// Run before Vite. Its dependency middleware otherwise sets immutable caching.
function noStore(_request, response, next) {
  const writeHead = response.writeHead;
  response.writeHead = function (...args) {
    const headers = typeof args[1] === "string" ? args[2] : args[1];
    if (Array.isArray(headers)) {
      for (let index = headers.length - 2; index >= 0; index -= 2) {
        if (/^(cache-control|expires|pragma)$/i.test(headers[index])) {
          headers.splice(index, 2);
        }
      }
    } else if (headers) {
      for (const name of Object.keys(headers)) {
        if (/^(cache-control|expires|pragma)$/i.test(name)) delete headers[name];
      }
    }
    this.setHeader("Cache-Control", "no-store");
    this.setHeader("Pragma", "no-cache");
    this.setHeader("Expires", "0");
    return writeHead.apply(this, args);
  };
  next();
}

/** Keep development and preview assets fresh without changing production output. */
export function developmentFreshness({ watchDirectories = [] } = {}) {
  if (watchDirectories.some((path) => !isAbsolute(path))) {
    throw new TypeError("Shared watch directories must be absolute paths.");
  }
  const generation = JSON.stringify(randomUUID());
  let dispose = () => {};
  return {
    name: "opendle-development-freshness",
    apply: "serve",
    config() {
      // Vite includes define values in its optimizer hash. A new server must not
      // reuse the URL of an old immutable dependency in a browser cache.
      return { define: { __OPENDLE_DEV_GENERATION__: generation } };
    },
    configureServer(server) {
      server.middlewares.use(noStore);
      let timer;
      const watchers = [];
      const close = () => {
        clearTimeout(timer);
        for (const watcher of watchers) watcher.close();
      };
      dispose = close;
      try {
        for (const directory of new Set(watchDirectories)) {
          // Vite excludes node_modules from its normal watcher. Shared build
          // directories need a separate watcher, also when Docker mounts them.
          const watcher = watch(directory, { recursive: true }, () => {
            clearTimeout(timer);
            timer = setTimeout(() => {
              server.moduleGraph.invalidateAll();
              server.ws.send({ type: "full-reload" });
            }, 100);
          });
          watcher.on("error", (error) => server.config.logger.error(error.message));
          watchers.push(watcher);
        }
      } catch (error) {
        close();
        throw error;
      }
      server.httpServer?.once("close", close);
    },
    closeBundle() {
      // Middleware mode has no HTTP server. Vite still closes the plugin bundle.
      dispose();
    },
    configurePreviewServer(server) {
      server.middlewares.use(noStore);
    },
  };
}
