import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { PlaywrightExtensionDriver } from '../src/mcp-driver.mjs';
import { CAPABILITIES, toolMapping } from '../src/protocol.mjs';

const { wsServer: WebSocketServer } = createRequire(import.meta.url)('playwright-core/lib/utilsBundle');
const packageSpec = '@playwright/mcp@0.0.83';

test('locked real MCP process advertises every negotiated host operation without opening Chrome', { timeout: 15000 }, async () => {
  const driver = new PlaywrightExtensionDriver({ packageSpec, profileDirName: 'Default' });
  try {
    await driver.connect();
    for (const action of CAPABILITIES) {
      const mapped = toolMapping(action, { url: 'https://example.com', ref: 'e1', text: 'plain', direction: 'down', key: 'Enter' });
      assert.ok(driver.schemas.has(mapped.name), `${action} must have a real MCP driver`);
    }
  } finally {
    await driver.close();
  }
  const mismatch = new PlaywrightExtensionDriver({ packageSpec: '@playwright/mcp@0.0.1', profileDirName: 'Default' });
  await assert.rejects(mismatch.connect(), /does not match/);
});

test('actual process entry exchanges a Desktop grant, attaches once and answers heartbeat', { timeout: 15000 }, async t => {
  let registered;
  const server = createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    registered = { headers: req.headers, body: JSON.parse(Buffer.concat(chunks)) };
    res.writeHead(201, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ protocol_version: 1, ticket: 'single-use-test-ticket',
      ws_path: '/v1/browser-control/ws', scope: registered.body }));
  });
  const wss = new WebSocketServer({ server, handleProtocols: protocols => {
    assert.ok(protocols.has('hermes-browser-control-ticket.single-use-test-ticket'));
    return 'hermes-browser-control-v1';
  } });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => { wss.close(); server.close(); });
  const connected = once(wss, 'connection');
  const env = {};
  for (const key of ['PATH', 'Path', 'USERPROFILE', 'HOME', 'APPDATA', 'LOCALAPPDATA', 'SYSTEMROOT', 'TEMP', 'TMP']) {
    if (process.env[key]) env[key] = process.env[key];
  }
  Object.assign(env, { STARDUST_SESSION_ID: 'owned-session', STARDUST_CONTROLLER_ID: 'owned-controller',
    STARDUST_BROWSER_PROFILE_ID: 'chrome-Default', STARDUST_BROWSER_CONTROL_GRANT: 'normal-grant',
    STARDUST_BROWSER_CAPABILITIES: JSON.stringify(CAPABILITIES), STARDUST_BROWSER_PROTOCOL_VERSION: '1',
    STARDUST_GATEWAY_URL: `http://127.0.0.1:${server.address().port}/`,
    STARDUST_CHROME_PROFILE_DIR: 'Default', PLAYWRIGHT_MCP_PACKAGE: packageSpec,
    ELECTRON_RUN_AS_NODE: '1' });
  const child = spawn(process.execPath, [fileURLToPath(new URL('../src/main.mjs', import.meta.url))], {
    env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true,
  });
  t.after(() => child.kill());
  let output = '';
  let errors = '';
  child.stderr.on('data', chunk => { errors += chunk; });
  const ready = new Promise((resolve, reject) => {
    child.stdout.on('data', chunk => {
      output += chunk;
      if (output.includes('"event":"ready"')) resolve();
    });
    child.once('exit', code => reject(new Error(`Bridge exited ${code}: ${errors}`)));
  });
  const [socket] = await connected;
  t.after(() => socket.terminate());
  await ready;
  assert.equal(registered.headers['x-stardust-browser-control-grant'], 'normal-grant');
  assert.equal(registered.headers.authorization, undefined);
  assert.deepEqual(registered.body.capabilities, [...CAPABILITIES]);
  assert.equal(registered.body.session_id, 'owned-session');
  const reply = once(socket, 'message');
  socket.send(JSON.stringify({ method: 'browser.controller.heartbeat', params: { nonce: 'proof' } }));
  assert.deepEqual(JSON.parse((await reply)[0].toString()), {
    method: 'browser.controller.heartbeat', params: { nonce: 'proof', ok: true },
  });
});
