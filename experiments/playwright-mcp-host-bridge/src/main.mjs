import { StardustBrowserBrokerClient } from './broker-client.mjs';
import { PlaywrightExtensionDriver } from './mcp-driver.mjs';

const sessionId = process.env.STARDUST_SESSION_ID;
const token = process.env.STARDUST_API_SERVER_KEY;
const grant = process.env.STARDUST_BROWSER_CONTROL_GRANT;
const gateway = process.env.STARDUST_GATEWAY_URL || 'http://127.0.0.1:8642/';
const packageSpec = process.env.PLAYWRIGHT_MCP_PACKAGE;
const profileDirName = process.env.STARDUST_CHROME_PROFILE_DIR;
if (!sessionId || (!token && !grant) || !packageSpec || !profileDirName) {
  console.error('Set STARDUST_SESSION_ID, a token or one-time browser grant, STARDUST_CHROME_PROFILE_DIR, and an exact PLAYWRIGHT_MCP_PACKAGE version.');
  process.exit(2);
}
let client;
let driver;
async function shutdown() {
  if (client) await client.stop();
  else await driver?.close();
  console.log(JSON.stringify({ event: 'stopped' }));
  process.exit(0);
}
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);
try {
  const controllerId = process.env.STARDUST_CONTROLLER_ID;
  const browserProfileId = process.env.STARDUST_BROWSER_PROFILE_ID;
  const capabilitiesRaw = process.env.STARDUST_BROWSER_CAPABILITIES;
  const protocolVersion = process.env.STARDUST_BROWSER_PROTOCOL_VERSION;
  if (grant && (!controllerId || !browserProfileId)) throw new Error('One-time browser grant requires an exact controller and browser profile');
  if (protocolVersion && protocolVersion !== '1') throw new Error('Unsupported browser protocol version');
  const capabilities = capabilitiesRaw ? JSON.parse(capabilitiesRaw) : undefined;
  driver = new PlaywrightExtensionDriver({ packageSpec, profileDirName });
  await driver.connect();
  client = new StardustBrowserBrokerClient({
    gateway, token: token || undefined, grant, sessionId, controllerId, capabilities, browserProfileId: browserProfileId || `chrome-${profileDirName}`, driver,
    onDisconnect: grant ? () => {
      console.error(JSON.stringify({ event: 'error', message: 'Browser connection lost; pair again explicitly' }));
      client.stop().finally(() => { process.exitCode = 1; });
    } : undefined,
  });
  await client.connect();
  console.log(JSON.stringify({ event: 'ready', session_id: sessionId, controller_id: process.env.STARDUST_CONTROLLER_ID || null }));
} catch (e) {
  console.error(JSON.stringify({ event: 'error', message: String(e.message || e).replace(/grant|token|secret|bearer/gi, '[REDACTED]').slice(0, 320) }));
  if (client) await client.stop();
  else await driver?.close();
  process.exitCode = 1;
}
