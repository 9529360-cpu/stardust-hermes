import { StardustBrowserBrokerClient } from './broker-client.mjs';
import { PlaywrightExtensionDriver } from './mcp-driver.mjs';

const sessionId = process.env.STARDUST_SESSION_ID;
const token = process.env.STARDUST_API_SERVER_KEY;
const gateway = process.env.STARDUST_GATEWAY_URL || 'http://127.0.0.1:8642/';
const packageSpec = process.env.PLAYWRIGHT_MCP_PACKAGE;
const profileDirName = process.env.STARDUST_CHROME_PROFILE_DIR;
if (!sessionId || !token || !packageSpec || !profileDirName) {
  console.error('Set STARDUST_SESSION_ID, STARDUST_API_SERVER_KEY, STARDUST_CHROME_PROFILE_DIR, and an exact PLAYWRIGHT_MCP_PACKAGE version.');
  process.exit(2);
}
let client;
async function shutdown() {
  await client?.stop();
  process.exit(0);
}
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);
try {
  const driver = new PlaywrightExtensionDriver({ packageSpec, profileDirName });
  await driver.connect();
  client = new StardustBrowserBrokerClient({
    gateway, token, sessionId, driver, browserProfileId: `chrome-${profileDirName}`,
  });
  await client.connect();
  console.log('Stardust Browser: authorized Playwright Chrome connection active for this session.');
} catch (e) {
  console.error('Browser pairing failed:', String(e.message || e));
  await client?.stop();
  process.exitCode = 1;
}
