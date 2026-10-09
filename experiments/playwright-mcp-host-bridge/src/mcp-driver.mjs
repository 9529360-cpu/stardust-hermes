/** The only browser engine used here is Microsoft's Playwright MCP + official Chrome extension. */
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { normalizeResult, toolMapping } from './protocol.mjs';

export class PlaywrightExtensionDriver {
  constructor({ packageSpec = process.env.PLAYWRIGHT_MCP_PACKAGE,
                profileDirName = process.env.STARDUST_CHROME_PROFILE_DIR } = {}) {
    // Exact package version must be smoke-tested/pinned when integrated into the repo.
    if (!/^@playwright\/mcp@\d+\.\d+\.\d+$/.test(packageSpec)) {
      throw new Error('PLAYWRIGHT_MCP_PACKAGE must specify an exact tested version');
    }
    if (typeof profileDirName !== 'string' || !/^[\w .-]{1,90}$/.test(profileDirName)) {
      throw new Error('Explicit Chrome profile directory name is required, e.g. Default or Profile 1');
    }
    this.packageSpec = packageSpec;
    this.profileDirName = profileDirName;
    this.client = null;
    this.schemas = new Map();
  }

  async connect() {
    const [{ Client }, { StdioClientTransport }] = await Promise.all([
      import('@modelcontextprotocol/sdk/client/index.js'),
      import('@modelcontextprotocol/sdk/client/stdio.js'),
    ]);
    const client = new Client({ name: 'stardust-host-controller', version: '0.1.0' }, { capabilities: {} });
    // Never pass STARDUST_API_SERVER_KEY or unrelated local secrets to the
    // Playwright subprocess. Connection permission remains user-mediated by
    // the official Chrome extension (no auto-approval token inherited).
    const childEnv = {};
    for (const name of ['PATH', 'Path', 'HOME', 'USERPROFILE', 'APPDATA',
                        'LOCALAPPDATA', 'SYSTEMROOT', 'TEMP', 'TMP', 'TMPDIR']) {
      if (process.env[name]) childEnv[name] = process.env[name];
    }
    // Desktop's executable is Electron, including in a packaged install.
    childEnv.ELECTRON_RUN_AS_NODE = '1';
    // Launch the local, lockfile-installed package directly. npx could download
    // a fresh executable or resolve a different global version on Windows.
    const manifestPath = createRequire(import.meta.url).resolve('@playwright/mcp/package.json');
    const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
    if (`@playwright/mcp@${manifest.version}` !== this.packageSpec) {
      throw new Error('Installed Playwright MCP does not match the pinned package version');
    }
    const cliPath = resolve(dirname(manifestPath), manifest.bin['playwright-mcp']);
    const transport = new StdioClientTransport({
      command: process.execPath,
      args: [cliPath, '--extension', '--profile-dir-name', this.profileDirName, '--caps', 'vision'],
      env: childEnv,
      stderr: 'inherit',
    });
    try {
      await client.connect(transport);
      const catalog = await client.listTools();
      this.schemas = new Map((catalog.tools || []).map(t => [t.name, t.inputSchema]));
      const required = ['browser_navigate', 'browser_snapshot', 'browser_click', 'browser_type',
        'browser_mouse_wheel', 'browser_press_key', 'browser_navigate_back', 'browser_tabs'];
      if (required.some(name => !this.schemas.has(name))) {
        throw new Error('Playwright MCP did not advertise the required Chrome extension tools');
      }
      this.client = client;
    } catch (error) {
      await client.close();
      throw error;
    }
  }

  async run(action, args) {
    if (!this.client) throw new Error('Playwright Chrome connection is not initialized');
    const candidate = toolMapping(action, args, this.schemas.get(action));
    const schema = this.schemas.get(candidate.name);
    if (!schema) throw new Error(`Playwright MCP does not support: ${candidate.name}`);
    // Resolve variant 'target' vs 'ref' against the actual installed MCP version.
    const resolved = toolMapping(action, args, schema);
    try {
      const result = await this.client.callTool({ name: resolved.name, arguments: resolved.arguments });
      return normalizeResult(result);
    } catch (error) {
      throw new Error(String(error?.message || error).replace(/(?:grant|token|secret|authorization|bearer)[^ ]*/gi, '[REDACTED]').slice(0, 500));
    }
  }

  async close() { await this.client?.close(); this.client = null; }
}
