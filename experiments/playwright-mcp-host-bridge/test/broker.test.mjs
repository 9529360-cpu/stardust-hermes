import test from 'node:test';
import assert from 'node:assert/strict';
import { StardustBrowserBrokerClient } from '../src/broker-client.mjs';

class FakeSocket extends EventTarget {
  static OPEN=1;
  constructor(url,protocols) {
    super(); this.url=url;this.protocols=protocols;this.readyState=0;this.sent=[];
    FakeSocket.instances.push(this);
    queueMicrotask(() => {this.readyState=1;this.dispatchEvent(new Event('open'));});
  }
  send(v) {this.sent.push(JSON.parse(v));}
  receive(frame) {this.dispatchEvent(new MessageEvent('message',{data:JSON.stringify(frame)}));}
  close() {this.readyState=3;this.dispatchEvent(new Event('close'));}
}
FakeSocket.instances=[];
function setup({driverRun=async (name,args)=>({success:true,name,args}), negotiated=['browser_snapshot','browser_click']}={}) {
  FakeSocket.instances=[];
  const requests=[];
  const fetchImpl=async (url,opts)=>{ requests.push({url:url.toString(),opts}); return {status:201,json:async()=>({
    protocol_version:1,ticket:'single-use-ticket',ws_path:'/v1/browser-control/ws',
    scope:{session_id:'owned-s',controller_id:'owned-c',browser_profile_id:'owned-p',capabilities:negotiated},
  })};};
  const driver={run:driverRun,close:async()=>{}};
  const client=new StardustBrowserBrokerClient({gateway:'http://127.0.0.1:8642/',token:'sensitive-bearer',
    sessionId:'owned-s',controllerId:'owned-c',browserProfileId:'owned-p',driver,
    fetchImpl,WS:FakeSocket,retryMax:0});
  return {client,requests,driver};
}
async function flush(client){await client.pending;await new Promise(resolve=>setImmediate(resolve));}

test('ticket registration uses Bearer only for loopback, ticket only in WebSocket subprotocol',async()=>{
  const {client,requests}=setup();await client.connect();
  const ws=FakeSocket.instances[0];
  assert.equal(requests[0].opts.headers.Authorization,'Bearer sensitive-bearer');
  assert.equal(ws.url,'ws://127.0.0.1:8642/v1/browser-control/ws');
  assert.deepEqual(ws.protocols,['hermes-browser-control-v1','hermes-browser-control-ticket.single-use-ticket']);
  assert.equal(ws.url.includes('ticket'),false);
  await client.stop();assert.equal(client.token,'');
});

test('receives exactly one command, acknowledges same command ID, leaves other actions untouched',async()=>{
  const {client}=setup();await client.connect();const ws=FakeSocket.instances[0];
  ws.receive({method:'browser.controller.command',params:{command_id:'cmd-1',action:'browser_snapshot',
    arguments:{},tool_call_id:'call-1'}});
  await flush(client);
  assert.deepEqual(ws.sent[0],{method:'browser.controller.result',params:{command_id:'cmd-1',ok:true,
    result:{success:true,name:'browser_snapshot',args:{}}}});
  ws.receive({method:'browser.controller.command',params:{command_id:'cmd-2',action:'browser_evaluate',
    arguments:{code:'danger()'}}});
  await flush(client);
  assert.equal(ws.sent.length,1);
  await client.stop();
});

test('controller failure fails closed and never invokes alternate browser',async()=>{
  const {client}=setup({driverRun:async()=>{throw new Error('site access revoked')}});
  await client.connect();const ws=FakeSocket.instances[0];
  ws.receive({method:'browser.controller.command',params:{command_id:'failing',action:'browser_click',
    arguments:{ref:'e1'},tool_call_id:'call-2'}});
  await flush(client);
  assert.deepEqual(ws.sent[0],{method:'browser.controller.result',params:{command_id:'failing',ok:false,
    error:'site access revoked'}});
  await client.stop();
});

test('reconnection does not re-execute in-flight work or accept stale results',async()=>{
  let resolveAction;let count=0;
  const {client}=setup({driverRun:async()=>{count++;return await new Promise(r=>resolveAction=r);}});
  await client.connect();const ws=FakeSocket.instances[0];
  ws.receive({method:'browser.controller.command',params:{command_id:'old',action:'browser_click',
    arguments:{ref:'e1'},tool_call_id:'once'}});
  await new Promise(r=>setImmediate(r));
  ws.close();
  resolveAction({success:true}); await flush(client);
  assert.equal(ws.sent.length,0);
  assert.equal(count,1);
  await client.stop();
});

test('cancel frame suppresses late result, malformed frames are ignored',async()=>{
  let finish;
  const {client}=setup({driverRun:async()=>await new Promise(r=>finish=r)});
  await client.connect();const ws=FakeSocket.instances[0];
  ws.receive({method:'browser.controller.command',params:{command_id:'c1',action:'browser_snapshot',
    arguments:{},tool_call_id:'cancel-me'}});
  await new Promise(r=>setImmediate(r));
  ws.receive({method:'browser.controller.cancel',params:{tool_call_id:'cancel-me'}});
  finish({success:true});await flush(client);
  assert.deepEqual(ws.sent,[]);
  ws.receive({method:'browser.controller.command',params:{command_id:'x',action:'browser_click',arguments:[]}});
  ws.receive({method:'browser.controller.command',params:{command_id:'x',action:'browser_evaluate',arguments:{}}});
  await flush(client);assert.deepEqual(ws.sent,[]);
  await client.stop();
});


test('duplicate command IDs never cause a second click', async()=>{
  let clicks=0;
  const {client}=setup({driverRun:async()=>{clicks++;return {success:true};}});
  await client.connect();const ws=FakeSocket.instances[0];
  const frame={method:'browser.controller.command',params:{command_id:'idem-key',
    action:'browser_click',arguments:{ref:'@e1'},tool_call_id:'t-123'}};
  ws.receive(frame); ws.receive(frame); await flush(client);
  assert.equal(clicks,1);
  assert.equal(ws.sent.length,1);
  await client.stop();
});
