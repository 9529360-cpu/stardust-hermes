/** Hermes v1 browser controller protocol; independent of browser/transport libs. */
const PROTOCOL_VERSION = 1;
export const CAPABILITIES = Object.freeze([
  'browser_navigate', 'browser_snapshot', 'browser_click', 'browser_type',
  'browser_scroll', 'browser_press', 'browser_back', 'browser_tabs',
]);
const CAPSET = new Set(CAPABILITIES);
const MAX_RESULT_CHARS = 120_000;

export function validateGateway(url) {
  // Never send API_SERVER_KEY to a cloud host or arbitrary remote endpoint.
  const parsed = new URL(url);
  if (parsed.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(parsed.hostname) ||
      parsed.username || parsed.password || parsed.search || parsed.hash || parsed.pathname !== '/') {
    throw new Error('Only loopback HTTP gateway roots are supported, e.g. http://127.0.0.1:8642/');
  }
  return parsed;
}

export function requestRegistration({ sessionId, controllerId, browserProfileId }) {
  for (const value of [sessionId, controllerId, browserProfileId]) {
    if (typeof value !== 'string' || !value.trim() || value.length > 128) {
      throw new Error('Browser registration requires bounded session, controller and profile identifiers');
    }
  }
  return {
    protocol_version: PROTOCOL_VERSION,
    session_id: sessionId,
    controller_id: controllerId,
    browser_profile_id: browserProfileId,
    capabilities: [...CAPABILITIES],
  };
}

export function validateRegistration(data, requested) {
  if (!data || data.protocol_version !== PROTOCOL_VERSION || typeof data.ticket !== 'string' ||
      !/^[\w-]+$/.test(data.ticket) || data.ticket.length > 500 ||
      data.ws_path !== '/v1/browser-control/ws' || !data.scope ||
      data.scope.session_id !== requested.session_id ||
      data.scope.controller_id !== requested.controller_id ||
      data.scope.browser_profile_id !== requested.browser_profile_id ||
      !Array.isArray(data.scope.capabilities)) {
    throw new Error('Gateway controller registration was not scoped to the requested session/identity');
  }
  const negotiated = data.scope.capabilities;
  if (negotiated.some(cap => !CAPSET.has(cap)) || negotiated.length === 0) {
    throw new Error('Gateway supplied unsupported browser capabilities');
  }
  return new Set(negotiated);
}

export function gatewayUrlForWebSocket(base) {
  const parsed = validateGateway(base);
  parsed.protocol = 'ws:';
  parsed.pathname = '/v1/browser-control/ws';
  return parsed.toString();
}

export function toolMapping(action, args = {}, inputSchema = {}) {
  if (!CAPSET.has(action)) throw new Error(`Unsupported capability: ${action}`);
  if (args === null || typeof args !== 'object' || Array.isArray(args)) {
    throw new Error('Command arguments must be an object');
  }
  const props = inputSchema?.properties || {};
  const refKey = 'target' in props ? 'target' : ('ref' in props ? 'ref' : 'target');
  const ref = typeof args.ref === 'string' ? args.ref : '';
  switch (action) {
    case 'browser_navigate': {
      const url = new URL(args.url);
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) {
        throw new Error('Only credential-free HTTP(S) URLs are supported');
      }
      return { name: 'browser_navigate', arguments: { url: url.toString() } };
    }
    case 'browser_snapshot': return { name: 'browser_snapshot', arguments: {} };
    case 'browser_click':
    case 'browser_type': {
      if (!ref) throw new Error(`${action} needs a ref from this exact browser session`);
      const a = { [refKey]: ref };
      if ('element' in props) a.element = ref;
      if (action === 'browser_type') {
        if (typeof args.text !== 'string') throw new Error('browser_type needs text');
        a.text = args.text;
        if ('submit' in props) a.submit = args.submit === true;
      }
      return { name: action, arguments: a };
    }
    case 'browser_scroll': {
      const direction = args.direction === 'up' ? -1 : (args.direction === 'down' ? 1 : null);
      if (!direction) throw new Error('scroll direction must be up or down');
      const amount = Number.isInteger(args.amount) && Math.abs(args.amount) <= 2000 ? Math.abs(args.amount) : 500;
      return { name: 'browser_mouse_wheel', arguments: { deltaY: direction * amount, deltaX: 0 } };
    }
    case 'browser_press': {
      if (typeof args.key !== 'string' || !/^[\w+ -]{1,36}$/.test(args.key)) {
        throw new Error('Invalid keyboard key');
      }
      return { name: 'browser_press_key', arguments: { key: args.key } };
    }
    case 'browser_back': return { name: 'browser_navigate_back', arguments: {} };
    case 'browser_tabs': return { name: 'browser_tabs', arguments: { action: 'list' } };
    default: throw new Error('Unsupported action');
  }
}

export function normalizeResult(reply) {
  if (!reply || reply.isError) {
    throw new Error(String(reply?.content?.find?.(x => x.type === 'text')?.text || 'Playwright MCP action failed').slice(0,500));
  }
  // Exclude potentially large/base64/sensitive image blobs from the LLM/broker
  // until artifact transport is wired through existing Stardust facilities.
  const blocks = (reply.content || []).filter(x => x?.type === 'text').map(x => String(x.text || ''));
  return { success: true, text: blocks.join('\n').slice(0, MAX_RESULT_CHARS) };
}
