import { readFileSync } from 'node:fs'
import http from 'node:http'
import vm from 'node:vm'

import { afterEach, expect, test } from 'vitest'

import { downloadImage as download } from './image-download'

const source = readFileSync(new URL('./main.ts', import.meta.url), 'utf8')
const servers: http.Server[] = []

function originalFunction(name: string, next: string, globals: Record<string, unknown>) {
  const start = source.indexOf(`function ${name}(`)
  const code = source.slice(start, source.indexOf(next, start))

  return vm.runInNewContext(`(${name === 'resourceBufferFromUrl' ? 'async ' : ''}${code})`, {
    URL,
    Buffer,
    setTimeout,
    clearTimeout,
    ...globals
  })
}

async function serve(handler: http.RequestListener) {
  const server = http.createServer(handler)
  servers.push(server)
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
  const address = server.address() as { port: number }

  return `http://127.0.0.1:${address.port}`
}

afterEach(async () => {
  await Promise.all(
    servers.splice(0).map(
      server =>
        new Promise<void>(resolve => {
          server.closeAllConnections()
          server.close(() => resolve())
        })
    )
  )
})

test.each(['%', '%E0%A4%A', '%FF'])('malformed deep-link encoding is discarded: %s', encoded => {
  const dispatch = originalFunction('handleDeepLink', '\n// Renderer calls this', {
    DEEPLINK_SCHEMES: ['stardust', 'hermes'],
    rememberLog: () => {},
    _rendererReadyForDeepLink: false,
    mainWindow: null
  })

  expect(() => dispatch(`stardust://blueprint/${encoded}`)).not.toThrow()
})

test('valid Unicode deep links retain their decoded payload', () => {
  const sent: unknown[] = []

  const dispatch = originalFunction('handleDeepLink', '\n// Renderer calls this', {
    DEEPLINK_SCHEMES: ['stardust', 'hermes'],
    rememberLog: () => {},
    _rendererReadyForDeepLink: true,
    mainWindow: {
      isDestroyed: () => false,
      isMinimized: () => false,
      focus: () => {},
      webContents: { send: (...args: unknown[]) => sent.push(args) }
    }
  })

  dispatch('stardust://blueprint/%E6%98%9F%E5%B0%98?slot=value')
  expect(sent).toEqual([['stardust:deep-link', { kind: 'blueprint', name: '星尘', params: { slot: 'value' } }]])
})

test.each([301, 302, 303, 307, 308])('HTTP %s redirects fetch the final image bytes and MIME type', async status => {
  const image = Buffer.from('image-bytes')

  const base = await serve((req, res) => {
    if (req.url === '/redirect') {
      res.writeHead(status, { Location: '/image' }).end()
    } else {
      res.writeHead(200, { 'Content-Type': 'image/png' }).end(image)
    }
  })

  const result = await download(`${base}/redirect`)
  expect(result.buffer.equals(image)).toBe(true)
  expect(result.mimeType).toBe('image/png')
})

test('remote image streams have a cumulative byte limit', async () => {
  const base = await serve((_req, res) => {
    res.writeHead(200)
    res.end(Buffer.alloc(1025))
  })

  await expect(download(base, { maxBytes: 1024 })).rejects.toThrow(/size|large|limit/i)
})

test('remote image requests have a total deadline even before response headers', async () => {
  const base = await serve(() => {})

  const outcome = await Promise.race([
    download(base, { timeoutMs: 50 }).then(
      () => 'resolved',
      () => 'rejected'
    ),
    new Promise(resolve => setTimeout(() => resolve('hung'), 200))
  ])

  expect(outcome).toBe('rejected')
})

test('redirect loops stop at the hop limit', async () => {
  let hits = 0

  const base = await serve((_req, res) => {
    hits++
    res.writeHead(302, { Location: '/loop' }).end()
  })

  await expect(download(base, { maxRedirects: 2 })).rejects.toThrow(/redirect limit/i)
  expect(hits).toBe(3)
})

test.each(['file:///secret.png', 'ftp://example.com/image', 'http://user:pass@127.0.0.1/image'])(
  'redirect targets are validated: %s',
  async location => {
    const base = await serve((_req, res) => res.writeHead(302, { Location: location }).end())
    await expect(download(base)).rejects.toThrow(/protocol|credentials/i)
  }
)

test('redirects can cross origins without forwarding credentials', async () => {
  const target = await serve((req, res) => {
    expect(req.headers.authorization).toBeUndefined()
    res.end('image')
  })

  const base = await serve((_req, res) => res.writeHead(302, { Location: target }).end())
  expect((await download(base)).buffer.toString()).toBe('image')
})

test.each([204, 304, 404, 500])('empty and unsuccessful HTTP %s responses cannot be saved', async status => {
  const base = await serve((_req, res) => res.writeHead(status).end())
  await expect(download(base)).rejects.toThrow(/empty|HTTP/i)
})

test('Content-Length rejects oversized images before buffering', async () => {
  const base = await serve((_req, res) => res.writeHead(200, { 'Content-Length': 2048 }).end('x'))
  await expect(download(base, { maxBytes: 1024 })).rejects.toThrow(/size limit/i)
})

test('deadline also stops an active body stream and closes the connection', async () => {
  let closed = false

  const base = await serve((_req, res) => {
    res.write('x')
    const interval = setInterval(() => res.write('x'), 5)
    res.on('close', () => {
      closed = true
      clearInterval(interval)
    })
  })

  await expect(download(base, { timeoutMs: 50 })).rejects.toThrow(/timed out/i)
  await new Promise(resolve => setTimeout(resolve, 20))
  expect(closed).toBe(true)
})
