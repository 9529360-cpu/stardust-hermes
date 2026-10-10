import test from 'node:test';
import assert from 'node:assert/strict';
import { PlaywrightExtensionDriver } from '../src/mcp-driver.mjs';

test('requires explicit pinned Playwright MCP; never auto-downloads latest unreviewed binary',()=>{
  assert.throws(()=>new PlaywrightExtensionDriver({profileDirName:'Default'}), /exact tested version/);
  assert.throws(()=>new PlaywrightExtensionDriver({packageSpec:'@playwright/mcp@latest',profileDirName:'Default'}));
  assert.equal(new PlaywrightExtensionDriver({packageSpec:'@playwright/mcp@0.0.1',profileDirName:'Default'}).packageSpec,
    '@playwright/mcp@0.0.1');
});

test('driver resolves actual MCP ref schema and delegates to official Playwright tool',async()=>{
  const d=new PlaywrightExtensionDriver({packageSpec:'@playwright/mcp@0.0.1',profileDirName:'Default'});
  const invoked=[];
  d.schemas=new Map([['browser_click',{properties:{ref:{},element:{}}}],
    ['browser_snapshot',{properties:{}}],['browser_navigate',{properties:{url:{}}}]]);
  d.client={callTool:async item=>{invoked.push(item);return {content:[{type:'text',text:'page visible'}]}}};
  assert.deepEqual(await d.run('browser_click',{ref:'@e1'}),{success:true,text:'page visible'});
  assert.deepEqual(invoked,[{name:'browser_click',arguments:{ref:'@e1',element:'@e1'}}]);
  assert.deepEqual(await d.run('browser_snapshot',{}),{success:true,text:'page visible'});
  await assert.rejects(d.run('browser_scroll',{direction:'down'}),/does not support/);
});
