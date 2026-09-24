import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { $backdrop, $backdropImagePath } from '@/store/backdrop'

const assetPath = (path: string) => `${import.meta.env.BASE_URL}${path.replace(/^\\/+/, '')}`

export function Backdrop() {
  const on = useStore($backdrop)
  const imagePath = useStore($backdropImagePath)
  const [customSrc, setCustomSrc] = useState<null | string>(null)

  useEffect(() => {
    let canceled = false

    if (!on || !imagePath || !window.hermesDesktop?.readFileDataUrl) {
      setCustomSrc(null)

      return () => {
        canceled = true
      }
    }

    setCustomSrc(null)

    void window.hermesDesktop
      .readFileDataUrl(imagePath)
      .then(src => {
        if (!canceled && src.startsWith('data:image/')) {
          setCustomSrc(src)
        }
      })
      .catch(() => {
        if (!canceled) {
          setCustomSrc(null)
        }
      })

    return () => {
      canceled = true
    }
  }, [imagePath, on])

  useEffect(() => {
    const root = document.documentElement

    if (!on) {
      delete root.dataset.stardustWindowBackdrop
    } else {
      root.dataset.stardustWindowBackdrop = customSrc ? 'custom' : 'bundled'
    }

    return () => {
      delete root.dataset.stardustWindowBackdrop
    }
  }, [customSrc, on])

  if (!on) {
    return null
  }

  const custom = Boolean(customSrc)

  return (
    <div
      aria-hidden
      className="pointer-events-none absolute inset-0 z-[-1] overflow-hidden"
      data-custom-backdrop={custom ? 'true' : undefined}
      data-window-backdrop=""
    >
      <img
        alt=""
        className={
          custom
            ? 'h-full w-full object-cover object-center'
            : 'h-[160dvh] w-auto min-w-dvw object-cover object-left-top opacity-[0.025] mix-blend-difference [filter:invert(var(--backdrop-invert-mul,1))]'
        }
        fetchPriority="low"
        src={customSrc ?? assetPath('ds-assets/filler-bg0.jpg')}
      />
      {custom && (
        <div
          aria-hidden
          className="absolute inset-0 bg-[color-mix(in_srgb,var(--ui-bg-chrome)_14%,transparent)]"
          data-window-backdrop-tint=""
        />
      )}
    </div>
  )
}
