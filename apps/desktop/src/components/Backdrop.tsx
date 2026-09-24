import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { $backdrop, $backdropImagePath } from '@/store/backdrop'

const assetPath = (path: string) => `${import.meta.env.BASE_URL}${path.replace(/^\/+/, '')}`

export function Backdrop() {
  const on = useStore($backdrop)
  const imagePath = useStore($backdropImagePath)
  const [customSrc, setCustomSrc] = useState<null | string>(null)

  useEffect(() => {
    let canceled = false

    if (!imagePath || !window.hermesDesktop?.readFileDataUrl) {
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
  }, [imagePath])

  if (!on) {
    return null
  }

  const custom = Boolean(customSrc)

  return (
    <div
      aria-hidden
      className={
        custom
          ? 'pointer-events-none absolute inset-0 z-2 overflow-hidden opacity-[0.34]'
          : 'pointer-events-none absolute inset-0 z-2 opacity-[0.025] mix-blend-difference'
      }
      data-custom-backdrop={custom ? 'true' : undefined}
    >
      <img
        alt=""
        className={
          custom
            ? 'h-full w-full object-cover object-center'
            : 'h-[160dvh] w-auto min-w-dvw object-cover object-left-top [filter:invert(var(--backdrop-invert-mul,1))]'
        }
        fetchPriority="low"
        src={customSrc ?? assetPath('ds-assets/filler-bg0.jpg')}
      />
    </div>
  )
}
