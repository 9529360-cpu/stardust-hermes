import test from 'node:test';
import assert from 'node:assert/strict';
import {
  CAPABILITIES, validateGateway, requestRegistration, validateRegistration,
  gatewayUrlForWebSocket, toolMapping, normalizeResult,
} from '../src/protocol.mjs';

test('gateway accepts loopback only (no token leaks to remote origin)', () => {
  assert.equal(validateGateway('http://127.0.0.1:8642/').port, '8642');
  assert.equal(gatewayUrlForWebSocket('http://localhost:8642/'), 'ws://localhost:8642/v1/browser-control/ws');
  for (const u of ['http://evil.com:8642/', 'https://evil.com/',
    'http://127.0.0.1@evil.com/', 'http://admin:pwd@localhost:8642/',
    'http://localhost:8642/other', 'http://localhost:8642/?token=abc']) {
    assert.throws(() => validateGateway(u));
  }
});

test('registration must match existing gateway identity and supported capabilities', () => {
  const request = requestRegistration({sessionId:'s1', controllerId:'c1',browserProfileId:'p1'});
  assert.equal(request.protocol_version,1);
  assert.ok(request.capabilities.includes('browser_click'));
  const result = {protocol_version:1,ticket:'valid-ticket',ws_path:'/v1/browser-control/ws',
    scope: {session_id:'s1',controller_id:'c1',browser_profile_id:'p1',capabilities:['browser_click']}};
  assert.equal(validateRegistration(result,request).has('browser_click'), true);
  assert.throws(() => validateRegistration({...result,scope:{...result.scope,session_id:'other'}},request));
  assert.throws(() => validateRegistration({...result,scope:{...result.scope,capabilities:['browser_evaluate']}},request));
  assert.throws(() => validateRegistration({...result,ticket:'not valid;'},request));
});

test('tool mapping reuses actual Playwright MCP driver, never custom DOM engine', () => {
  assert.deepEqual(toolMapping('browser_click',{ref:'e1'},{properties:{target:{},element:{}}}),{
    name:'browser_click',arguments:{target:'e1',element:'e1'}});
  assert.deepEqual(toolMapping('browser_type',{ref:'e2',text:'plain'},{properties:{ref:{},text:{},submit:{}}}),{
    name:'browser_type',arguments:{ref:'e2',text:'plain',submit:false}});
  assert.deepEqual(toolMapping('browser_scroll',{direction:'up',amount:900}),{
    name:'browser_mouse_wheel',arguments:{deltaY:-900,deltaX:0}});
  assert.deepEqual(toolMapping('browser_back'),{name:'browser_navigate_back',arguments:{}});
  assert.deepEqual(toolMapping('browser_tabs'),{name:'browser_tabs',arguments:{action:'list'}});
  assert.throws(() => toolMapping('browser_navigate',{url:'file:///etc/passwd'}));
  assert.throws(() => toolMapping('browser_navigate',{url:'https://admin:password@example.com'}));
  assert.throws(() => toolMapping('browser_click',{ref:''}));
  assert.throws(() => toolMapping('browser_evaluate',{code:'fetch("/")'}));
  assert.ok(CAPABILITIES.length > 5);
});

test('MCP errors stop actions; raw image data is not sent as base64 to model', () => {
  assert.deepEqual(normalizeResult({content:[{type:'text',text:'ok'}, {type:'image',data:'should-never-leak'}]}),
    {success:true,text:'ok'});
  assert.throws(() => normalizeResult({isError:true,content:[{type:'text',text:'denied'}]}));
  assert.throws(() => normalizeResult(null));
});
