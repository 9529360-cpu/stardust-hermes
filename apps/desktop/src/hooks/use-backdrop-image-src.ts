import { useEffect, useState } from 'react'

/**
 * The data URL of a device-local backdrop image, read through the desktop
 * bridge. Null while disabled, while the path is empty, when the bridge is
 * unavailable, or when the file does not decode as an image. Shared by the
 * window backdrop and the personalization preview so both show the same bytes.
 */
export function useBackdropImageSrc(path: null | string, enabled = true): null | string {
  const [src, setSrc] = useState<null | string>(null)

  useEffect(() => {
    let canceled = false

    if (!enabled || !path || !window.stardustDesktop?.readFileDataUrl) {
      setSrc(null)

      return () => {
        canceled = true
      }
    }

    setSrc(null)

    void window.stardustDesktop
      .readFileDataUrl(path)
      .then(next => {
        if (!canceled && next.startsWith('data:image/')) {
          setSrc(next)
        }
      })
      .catch(() => {
        if (!canceled) {
          setSrc(null)
        }
      })

    return () => {
      canceled = true
    }
  }, [enabled, path])

  return src
}
