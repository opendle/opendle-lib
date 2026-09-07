import assert from "node:assert/strict";
import { EventEmitter, once } from "node:events";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { developmentFreshness } from "./vite.mjs";

test("each server has a new dependency cache key; builds are excluded", () => {
  const first = developmentFreshness();
  assert.equal(first.apply, "serve");
  assert.notDeepEqual(first.config(), developmentFreshness().config());
  assert.throws(() => developmentFreshness({ watchDirectories: ["relative"] }), TypeError);
});

for (const hook of ["configureServer", "configurePreviewServer"]) {
  test(`${hook} overrides downstream immutable cache headers`, async () => {
    let middleware;
    const server = createServer((request, response) => {
      middleware(request, response, () => {
        response.setHeader("Cache-Control", "public, max-age=31536000, immutable");
        response.writeHead(200, { "cache-control": "max-age=300", "X-Test": "kept" });
        response.end("fresh");
      });
    });
    developmentFreshness()[hook]({
      middlewares: { use: (value) => { middleware = value; } },
      httpServer: server,
      watcher: new EventEmitter(),
    });
    server.listen(0, "127.0.0.1");
    await once(server, "listening");
    try {
      const response = await fetch(`http://127.0.0.1:${server.address().port}`);
      assert.equal(response.headers.get("cache-control"), "no-store");
      assert.equal(response.headers.get("x-test"), "kept");
      assert.equal(response.headers.get("expires"), "0");
      assert.equal(await response.text(), "fresh");
    } finally {
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
    }
  });
}

test("shared build changes invalidate transformed modules and reload clients", async () => {
  const directory = await mkdtemp(join(tmpdir(), "opendle-freshness-"));
  const httpServer = new EventEmitter();
  const messages = new EventEmitter();
  let invalidations = 0;
  developmentFreshness({ watchDirectories: [directory] }).configureServer({
    middlewares: { use() {} }, httpServer, watcher: new EventEmitter(),
    moduleGraph: { invalidateAll() { invalidations += 1; } },
    ws: { send(message) { messages.emit("message", message); } },
    config: { logger: { error: assert.fail } },
  });
  try {
    const reloaded = once(messages, "message", { signal: AbortSignal.timeout(5000) });
    await writeFile(join(directory, "index.js"), "export const updated = true;");
    assert.deepEqual((await reloaded)[0], { type: "full-reload" });
    assert.equal(invalidations, 1);
  } finally {
    httpServer.emit("close");
    await rm(directory, { recursive: true, force: true });
  }
});
