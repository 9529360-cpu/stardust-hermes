/**
 * generate-icons.mjs - builds the Stardust raster icon set from the SVG masters.
 *
 * Masters (hand-authored, committed) in apps/desktop/assets/brand/:
 *   stardust-mark.svg       full-colour tile mark on a transparent background
 *   stardust-mark-mono.svg  single-colour white glyph for dark boot/intro surfaces
 *
 * Outputs (generated, committed, reproducible from the masters) in the same folder:
 *   png-1024.png, png-512.png, png-256.png, png-64.png, png-32.png
 *       full-bleed tile for general and Linux use
 *   apple-touch-icon-180.png
 *       square, fully opaque tile; iOS applies its own corner mask
 *   icon.ico
 *       PNG-in-ICO entries at 256, 64, 48, 32 and 16 px
 *   icon.icns
 *       ic07/ic08/ic09/ic10 PNG chunks (128-1024 px); the tile is inset to
 *       Apple's 824/1024 app-icon grid so it matches the macOS Dock scale
 *
 * Rasterizing (no image npm dependency):
 *   The script starts the Electron binary that apps/desktop already depends on
 *   and passes this same file as Electron's main script. Each job renders one
 *   SVG inside a hidden, transparent BrowserWindow and captures the exact pixel
 *   rectangle with webContents.capturePage(). Hardware acceleration is disabled
 *   and the device scale factor is forced to 1, so the output depends only on
 *   the masters and the pinned Electron build, not on the GPU or display scale.
 *   The binary is found through the electron package; set
 *   ELECTRON_OVERRIDE_DIST_PATH to an Electron dist folder when the package was
 *   installed without its binary download.
 *
 * Usage, from apps/desktop after npm ci:
 *   node scripts/brand/generate-icons.mjs
 */

import { spawn } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const SCRIPT_PATH = fileURLToPath(import.meta.url)
const BRAND_DIR = path.resolve(path.dirname(SCRIPT_PATH), '../../assets/brand')
const MARK_SVG = path.join(BRAND_DIR, 'stardust-mark.svg')

// The tile rectangles in stardust-mark.svg use this corner radius (viewBox 0 0 64 64).
const TILE_RADIUS = '14'
// Apple app-icon grid: an 824px tile centred on a 1024px canvas.
const MACOS_TILE_SCALE = 824 / 1024
const MACOS_TILE_OFFSET = (64 - 64 * MACOS_TILE_SCALE) / 2

const FULL_BLEED_SIZES = [1024, 512, 256, 64, 48, 32, 16]
const PNG_OUTPUT_SIZES = [1024, 512, 256, 64, 32]
const APPLE_TOUCH_SIZE = 180
const ICO_SIZES = [256, 64, 48, 32, 16]
const MACOS_ICNS = [
  { type: 'ic07', size: 128 },
  { type: 'ic08', size: 256 },
  { type: 'ic09', size: 512 },
  { type: 'ic10', size: 1024 }
]

const RENDER_TIMEOUT_MS = 120_000
const PAINT_POLL_ATTEMPTS = 100
const PAINT_POLL_MS = 30
const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))

function replaceExactly(text, search, replacement, expected) {
  const parts = text.split(search)
  if (parts.length - 1 !== expected) {
    throw new Error(`expected ${expected} occurrence(s) of ${search} in stardust-mark.svg, found ${parts.length - 1}`)
  }
  return parts.join(replacement)
}

function splitSvgDocument(svg) {
  const match = /^([\s\S]*?<svg\b[^>]*>)([\s\S]*)<\/svg>\s*$/.exec(svg)
  if (!match) throw new Error('stardust-mark.svg must contain a single <svg> document')
  return { open: match[1], inner: match[2] }
}

// Square variant: same artwork without the tile corner radius.
function squareVariant(mark) {
  return replaceExactly(mark, `rx="${TILE_RADIUS}"`, 'rx="0"', 2)
}

// macOS variant: the whole mark scaled onto the Apple icon grid.
function macosVariant(mark) {
  const { open, inner } = splitSvgDocument(mark)
  const offset = MACOS_TILE_OFFSET
  return `${open}<g transform="translate(${offset} ${offset}) scale(${MACOS_TILE_SCALE})">${inner}</g></svg>`
}

function pngDimensions(png) {
  if (!png.subarray(0, 8).equals(PNG_SIGNATURE)) throw new Error('rasterizer returned a non-PNG buffer')
  // IHDR is always the first chunk: width and height are big-endian at offsets 16 and 20.
  return { width: png.readUInt32BE(16), height: png.readUInt32BE(20) }
}

// ICO: 6-byte header, one 16-byte directory entry per image, then the PNG payloads.
function buildIco(entries) {
  const header = Buffer.alloc(6)
  header.writeUInt16LE(0, 0)
  header.writeUInt16LE(1, 2)
  header.writeUInt16LE(entries.length, 4)
  const directory = Buffer.alloc(16 * entries.length)
  let offset = header.length + directory.length
  entries.forEach(({ size, png }, index) => {
    const at = index * 16
    const dimension = size >= 256 ? 0 : size
    directory.writeUInt8(dimension, at)
    directory.writeUInt8(dimension, at + 1)
    directory.writeUInt8(0, at + 2)
    directory.writeUInt8(0, at + 3)
    directory.writeUInt16LE(1, at + 4)
    directory.writeUInt16LE(32, at + 6)
    directory.writeUInt32LE(png.length, at + 8)
    directory.writeUInt32LE(offset, at + 12)
    offset += png.length
  })
  return Buffer.concat([header, directory, ...entries.map(({ png }) => png)])
}

// ICNS: 'icns' + total length, then one (OSType, length, PNG) chunk per size. Lengths are big-endian.
function buildIcns(chunks) {
  const parts = chunks.map(({ type, png }) => {
    const chunkHeader = Buffer.alloc(8)
    chunkHeader.write(type, 0, 4, 'ascii')
    chunkHeader.writeUInt32BE(png.length + 8, 4)
    return Buffer.concat([chunkHeader, png])
  })
  const body = Buffer.concat(parts)
  const fileHeader = Buffer.alloc(8)
  fileHeader.write('icns', 0, 4, 'ascii')
  fileHeader.writeUInt32BE(body.length + 8, 4)
  return Buffer.concat([fileHeader, body])
}

function runElectron(binary, jobPath) {
  return new Promise((resolve, reject) => {
    const child = spawn(binary, [SCRIPT_PATH], {
      env: { ...process.env, STARDUST_BRAND_RASTER_JOB: jobPath },
      stdio: ['ignore', 'ignore', 'pipe']
    })
    let stderr = ''
    child.stderr.on('data', chunk => {
      stderr += chunk
    })
    const timer = setTimeout(() => child.kill(), RENDER_TIMEOUT_MS)
    child.on('error', error => {
      clearTimeout(timer)
      reject(new Error(`cannot start Electron at ${binary}: ${error.message}`))
    })
    child.on('close', code => {
      clearTimeout(timer)
      if (code === 0) resolve()
      else reject(new Error(`Electron rasterizer exited with code ${code}\n${stderr.trim()}`))
    })
  })
}

async function rasterize(renders, workDir) {
  // The electron package returns its binary path and honours ELECTRON_OVERRIDE_DIST_PATH.
  const electronBinary = createRequire(import.meta.url)('electron')
  const items = renders.map(({ id, size, svg }) => ({ id, size, svg, out: path.join(workDir, `${id}.png`) }))
  const jobPath = path.join(workDir, 'job.json')
  await writeFile(jobPath, JSON.stringify({ userData: path.join(workDir, 'profile'), items }))
  await runElectron(electronBinary, jobPath)

  const rasters = new Map()
  for (const { id, size, out } of items) {
    const png = await readFile(out)
    const { width, height } = pngDimensions(png)
    if (width !== size || height !== size) {
      throw new Error(`${id} rendered ${width}x${height}, expected ${size}x${size}`)
    }
    rasters.set(id, png)
  }
  return rasters
}

async function generate() {
  const mark = await readFile(MARK_SVG, 'utf8')
  const renders = [
    ...FULL_BLEED_SIZES.map(size => ({ id: `full-${size}`, size, svg: mark })),
    { id: `apple-${APPLE_TOUCH_SIZE}`, size: APPLE_TOUCH_SIZE, svg: squareVariant(mark) },
    ...MACOS_ICNS.map(({ size }) => ({ id: `macos-${size}`, size, svg: macosVariant(mark) }))
  ]

  const workDir = await mkdtemp(path.join(tmpdir(), 'stardust-brand-'))
  try {
    const rasters = await rasterize(renders, workDir)
    const outputs = [
      ...PNG_OUTPUT_SIZES.map(size => [`png-${size}.png`, rasters.get(`full-${size}`)]),
      [`apple-touch-icon-${APPLE_TOUCH_SIZE}.png`, rasters.get(`apple-${APPLE_TOUCH_SIZE}`)],
      ['icon.ico', buildIco(ICO_SIZES.map(size => ({ size, png: rasters.get(`full-${size}`) })))],
      ['icon.icns', buildIcns(MACOS_ICNS.map(({ type, size }) => ({ type, png: rasters.get(`macos-${size}`) })))]
    ]
    for (const [name, data] of outputs) {
      await writeFile(path.join(BRAND_DIR, name), data)
      console.log(`wrote ${name} (${data.length} bytes)`)
    }
  } finally {
    await rm(workDir, { recursive: true, force: true })
  }
}

// ---- Electron main process: runs only when this file is started by runElectron() ----

function hasVisiblePixels(image) {
  const bitmap = image.toBitmap()
  for (let i = 3; i < bitmap.length; i += 4) {
    if (bitmap[i] !== 0) return true
  }
  return false
}

async function capturePng(BrowserWindow, svg, size) {
  const windowSize = Math.max(size, 256)
  const win = new BrowserWindow({
    width: windowSize,
    height: windowSize,
    show: false,
    frame: false,
    transparent: true,
    useContentSize: true,
    paintWhenInitiallyHidden: true,
    webPreferences: { sandbox: true, contextIsolation: true }
  })
  try {
    const page =
      '<!doctype html><html><head><meta charset="utf-8"><style>' +
      `html,body{margin:0;background:transparent;overflow:hidden}svg{display:block;width:${size}px;height:${size}px}` +
      `</style></head><body>${svg}</body></html>`
    await win.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(page)}`)
    // A hidden window paints a moment after load; poll until the capture has real pixels.
    for (let attempt = 0; attempt < PAINT_POLL_ATTEMPTS; attempt += 1) {
      const image = await win.webContents.capturePage({ x: 0, y: 0, width: size, height: size })
      const { width, height } = image.getSize()
      if (width === size && height === size && hasVisiblePixels(image)) return image.toPNG()
      await sleep(PAINT_POLL_MS)
    }
    throw new Error(`Electron never painted a ${size}px capture`)
  } finally {
    win.destroy()
  }
}

async function rasterizeJob(app, BrowserWindow, job) {
  let exitCode = 1
  try {
    app.dock?.hide()
    for (const item of job.items) {
      await writeFile(item.out, await capturePng(BrowserWindow, item.svg, item.size))
    }
    exitCode = 0
  } catch (error) {
    process.stderr.write(`${error?.stack ?? error}\n`)
  } finally {
    app.exit(exitCode)
  }
}

// Everything before whenReady() runs synchronously while this module evaluates. Electron fires 'ready' only after
// the entry module finishes, and rejects GPU and switch changes once the app is ready, so no await may appear here.
function startElectronRaster() {
  const { app, BrowserWindow } = createRequire(import.meta.url)('electron')
  try {
    const job = JSON.parse(readFileSync(process.env.STARDUST_BRAND_RASTER_JOB, 'utf8'))
    app.disableHardwareAcceleration()
    app.commandLine.appendSwitch('force-device-scale-factor', '1')
    app.setPath('userData', job.userData)
    // Each capture destroys its window. Without this handler Electron would quit on the first close, exit 0 and skip the rest.
    app.on('window-all-closed', () => {})
    app.whenReady().then(() => rasterizeJob(app, BrowserWindow, job))
  } catch (error) {
    process.stderr.write(`${error?.stack ?? error}\n`)
    app.exit(1)
  }
}

if (process.versions.electron) {
  startElectronRaster()
} else {
  generate().catch(error => {
    console.error(`generate-icons: ${error.message}`)
    process.exitCode = 1
  })
}
