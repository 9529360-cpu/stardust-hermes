/** The only browser engine used here is Microsoft's Playwright MCP + official Chrome extension. */
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
    const transport = new StdioClientTransport({
      command: 'npx', args: ['--no-install', this.packageSpec, '--extension',
                            '--profile-dir-name', this.profileDirName],
      env: childEnv,
      stderr: 'inherit',
    });
    await client.connect(transport);
    const catalog = await client.listTools();
    this.schemas = new Map((catalog.tools || []).map(t => [t.name, t.inputSchema]));
    const required = ['browser_navigate', 'browser_snapshot', 'browser_click', 'browser_type'];
    if (required.some(name => !this.schemas.has(name))) {
      await client.close();
      throw new Error('Playwright MCP did not advertise the required Chrome extension tools');
    }
    this.client = client;
  }

  async run(action, args) {
    if (!this.client) throw new Error('Playwright Chrome connection is not initialized');
    const candidate = toolMapping(action, args, this.schemas.get(action));
    const schema = this.schemas.get(candidate.name);
    if (!schema) throw new Error(`Playwright MCP does not support: ${candidate.name}`);
    // Resolve variant 'target' vs 'ref' against the actual installed MCP version.
    const resolved = toolMapping(action, args, schema);
    const value = await this.client.callTool({ name: resolved.name, arguments: resolved.arguments });
    return normalizeResult(value);
  }

  async close() { await this.client?.close(); this.client = null; }
}
