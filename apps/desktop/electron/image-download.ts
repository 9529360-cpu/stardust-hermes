import http from 'node:http'
import https from 'node:https'

export interface ImageDownloadOptions {
  maxBytes?: number
  timeoutMs?: number
  maxRedirects?: number
}

/** One deadline covers DNS, headers, redirects and body; memory is bounded per download. */
export function downloadImage(
  rawUrl: string,
  { maxBytes = 32 * 1024 * 1024, timeoutMs = 30_000, maxRedirects = 5 }: ImageDownloadOptions = {}
): Promise<{ buffer: Buffer; mimeType: string }> {
  return new Promise((resolve, reject) => {
    let request: http.ClientRequest | undefined
    let response: http.IncomingMessage | undefined
    let settled = false
    const timer = setTimeout(() => fail(new Error('Image download timed out')), timeoutMs)

    function fail(error: Error) {
      if (settled) {
        return
      }
      settled = true
      clearTimeout(timer)
      response?.destroy()
      request?.destroy()
      reject(error)
    }

    function visit(url: URL, redirects: number) {
      if (url.protocol !== 'http:' && url.protocol !== 'https:') {
        fail(new Error('Unsupported image URL protocol'))

        return
      }

      if (url.username || url.password) {
        fail(new Error('Image URL credentials are not supported'))

        return
      }

      const client = url.protocol === 'https:' ? https : http
      request = client.get(url, res => {
        response = res
        const status = res.statusCode || 500
        res.on('error', fail)

        if ([301, 302, 303, 307, 308].includes(status)) {
          const location = res.headers.location

          if (!location || redirects >= maxRedirects) {
            fail(new Error('Image redirect missing Location or redirect limit exceeded'))

            return
          }

          let next: URL

          try {
            next = new URL(location, url)
          } catch {
            fail(new Error('Invalid image redirect URL'))

            return
          }

          // Do not drain an arbitrarily large redirect body into the connection pool.
          res.destroy()
          visit(next, redirects + 1)

          return
        }

        res.on('aborted', () => fail(new Error('Image download aborted')))

        if (status < 200 || status >= 300) {
          fail(new Error(`Failed to fetch image: HTTP ${status}`))

          return
        }

        if (Number(res.headers['content-length']) > maxBytes) {
          fail(new Error('Image exceeds download size limit'))

          return
        }

        const chunks: Buffer[] = []
        let bytes = 0
        res.on('data', (chunk: Buffer) => {
          if (settled) {
            return
          }
          bytes += chunk.length

          if (bytes > maxBytes) {
            chunks.length = 0
            fail(new Error('Image exceeds download size limit'))

            return
          }

          chunks.push(chunk)
        })
        res.on('end', () => {
          if (settled) {
            return
          }

          if (!bytes) {
            fail(new Error('Empty image response'))

            return
          }

          settled = true
          clearTimeout(timer)
          resolve({
            buffer: Buffer.concat(chunks, bytes),
            mimeType: res.headers['content-type'] || 'application/octet-stream'
          })
        })
      })
      request.on('error', fail)
    }

    try {
      visit(new URL(rawUrl), 0)
    } catch (error) {
      fail(error as Error)
    }
  })
}
