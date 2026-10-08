import { randomUUID } from 'node:crypto';
import {
  CAPABILITIES, validateGateway, requestRegistration,
  validateRegistration, gatewayUrlForWebSocket,
} from './protocol.mjs';

const WS_PROTOCOL = 'hermes-browser-control-v1';
const TICKET_PROTOCOL = 'hermes-browser-control-ticket.';
const MAX_FRAME_BYTES = 250_000;
const MAX_QUEUE = 16;

export class StardustBrowserBrokerClient {
  constructor({ gateway, token, sessionId, driver, fetchImpl = fetch, WS = WebSocket, retryMax = 3,
                controllerId = randomUUID(), browserProfileId = 'playwright-chrome-user-profile' }) {
    this.gateway = validateGateway(gateway).toString();
    if (!token || typeof token !== 'string') throw new Error('A session-scoped gateway bearer token is required');
    if (!driver || typeof driver.run !== 'function') throw new Error('An initialized Playwright driver is required');
    this.token = token;
    this.driver = driver;
    this.fetchImpl = fetchImpl;
    this.WS = WS;
    this.requested = requestRegistration({ sessionId, controllerId, browserProfileId });
    this.retryMax = retryMax;
    this.attempts = 0;
    this.socket = null;
    this.connected = false;
    this.stopped = false;
    this.inflight = 0;
    this.pending = Promise.resolve();
    this.canceled = new Set();
    this.seenCommands = new Set();
    this.generation = 0;
    this.error = null;
    this.negotiated = new Set();
  }

  async register() {
    const reply = await this.fetchImpl(new URL('/v1/browser-control/register', this.gateway), {
      method: 'POST', headers: {
        Authorization: `Bearer ${this.token}`, 'Content-Type': 'application/json',
      }, body: JSON.stringify(this.requested),
    });
    if (reply.status !== 201) throw new Error(`Stardust Browser registration refused (HTTP ${reply.status})`);
    const body = await reply.json();
    this.negotiated = validateRegistration(body, this.requested);
    return body.ticket;
  }

  async connect() {
    if (this.stopped) throw new Error('Browser controller was explicitly stopped');
    const ticket = await this.register();
    return await new Promise((resolve, reject) => {
      const generation = ++this.generation;
      const socket = new this.WS(gatewayUrlForWebSocket(this.gateway),
        [WS_PROTOCOL, `${TICKET_PROTOCOL}${ticket}`]);
      this.socket = socket;
      let opened = false;
      socket.addEventListener('open', () => {
        if (generation !== this.generation || this.stopped) { socket.close(); return; }
        opened = true;
        this.connected = true;
        this.attempts = 0;
        this.error = null;
        resolve();
      });
      socket.addEventListener('message', event => {
        if (generation !== this.generation || !this.connected || this.stopped) return;
        this.onMessage(event.data, generation);
      });
      socket.addEventListener('close', () => {
        if (generation !== this.generation) return;
        this.connected = false;
        this.socket = null;
        if (!opened) reject(new Error('Stardust controller WebSocket was rejected'));
        if (opened && !this.stopped) this.scheduleReconnect();
      });
      socket.addEventListener('error', () => {
        if (!opened) reject(new Error('Stardust controller WebSocket handshake failed'));
      });
    });
  }

  scheduleReconnect() {
    if (this.stopped || ++this.attempts > this.retryMax) {
      this.error = new Error('Stardust controller connection lost; restart pairing explicitly');
      return;
    }
    // Bounded retry. Gateway keeps the same identity and decides whether a
    // reconnect is a transport refresh; no command is re-executed automatically.
    const delay = Math.min(400 * (2 ** (this.attempts - 1)), 2_000);
    setTimeout(() => {
      if (!this.stopped && !this.connected) {
        this.connect().catch(e => { this.error = e; this.scheduleReconnect(); });
      }
    }, delay);
  }

  send(frame, generation) {
    if (generation !== this.generation || this.stopped || !this.connected ||
        this.socket?.readyState !== this.WS.OPEN) return false;
    this.socket.send(JSON.stringify(frame));
    return true;
  }

  onMessage(raw, generation) {
    if (typeof raw !== 'string' || raw.length > MAX_FRAME_BYTES) return;
    let frame;
    try { frame = JSON.parse(raw); } catch { return; }
    if (!frame || typeof frame !== 'object' || Array.isArray(frame)) return;
    const params = frame.params;
    if (!params || typeof params !== 'object' || Array.isArray(params)) return;

    if (frame.method === 'browser.controller.heartbeat') {
      const nonce = params.nonce;
      if (typeof nonce === 'string' && nonce.length >= 1 && nonce.length <= 128) {
        this.send({ method: 'browser.controller.heartbeat', params: { nonce, ok: true } }, generation);
      }
      return;
    }
    if (frame.method === 'browser.controller.cancel') {
      if (typeof params.tool_call_id === 'string' && params.tool_call_id.length <= 128) {
        this.canceled.add(params.tool_call_id);
        if (this.canceled.size > 512) this.canceled.delete(this.canceled.values().next().value);
      }
      return;
    }
    if (frame.method !== 'browser.controller.command') return;
    const commandId = params.command_id;
    const action = params.action;
    const toolCallId = params.tool_call_id || '';
    if (typeof commandId !== 'string' || !/^[\w-]{1,128}$/.test(commandId) ||
        typeof action !== 'string' || !this.negotiated.has(action) ||
        (toolCallId && (typeof toolCallId !== 'string' || toolCallId.length > 128)) ||
        !params.arguments || typeof params.arguments !== 'object' || Array.isArray(params.arguments)) return;

    if (this.seenCommands.has(commandId)) return;
    if (this.inflight >= MAX_QUEUE) {
      this.send({ method: 'browser.controller.result', params: {
        command_id: commandId, ok: false, error: 'Browser controller is busy',
      } }, generation);
      return;
    }
    this.seenCommands.add(commandId);
    if (this.seenCommands.size > 512) this.seenCommands.delete(this.seenCommands.values().next().value);
    ++this.inflight;
    const args = Object.freeze({ ...params.arguments });
    this.pending = this.pending.catch(() => {}).then(async () => {
      try {
        if (this.canceled.has(toolCallId)) return;
        const result = await this.driver.run(action, args);
        if (!this.canceled.has(toolCallId)) this.send({ method: 'browser.controller.result', params: {
          command_id: commandId, ok: true, result,
        } }, generation);
      } catch (e) {
        if (!this.canceled.has(toolCallId)) this.send({ method: 'browser.controller.result', params: {
          command_id: commandId, ok: false, error: String(e?.message || e).slice(0, 500),
        } }, generation);
      } finally {
        --this.inflight;
        this.canceled.delete(toolCallId);
      }
    });
  }

  async stop() {
    this.stopped = true;
    this.connected = false;
    ++this.generation;
    try {
      if (this.socket?.readyState === this.WS.OPEN) {
        this.socket.send(JSON.stringify({ method: 'browser.controller.detach', params: {} }));
      }
      this.socket?.close();
    } finally {
      this.socket = null;
      await this.driver.close?.();
      // No token is written to disk or logs; drop this reference on shutdown.
      this.token = '';
    }
  }
}
