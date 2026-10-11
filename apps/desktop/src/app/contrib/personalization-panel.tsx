import { useStore } from '@nanostores/react'

import { useGatewayRequest } from '@/app/gateway/hooks/use-gateway-request'
import { MODE_OPTIONS } from '@/app/settings/constants'
import { BackdropOpacitySlider } from '@/components/backdrop-opacity-slider'
import { Button } from '@/components/ui/button'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { Switch } from '@/components/ui/switch'
import { useBackdropImageSrc } from '@/hooks/use-backdrop-image-src'
import { useI18n } from '@/i18n'
import { selectDesktopPaths } from '@/lib/desktop-fs'
import { triggerHaptic } from '@/lib/haptics'
import {
  $backdrop,
  $backdropImagePath,
  $backdropOpacity,
  BACKDROP_IMAGE_EXTENSIONS,
  setBackdrop,
  setBackdropImagePath
} from '@/store/backdrop'
import { notifyError } from '@/store/notifications'
import { $petInfo } from '@/store/pet'
import { setPetEnabled } from '@/store/pet-gallery'
import { useTheme } from '@/themes/context'

// Quick controls for the look of the app. Everything here writes the same
// stores as Settings > Appearance, which keeps the detailed options.
export function PersonalizationPanel({ onOpenAppearance }: { onOpenAppearance: () => void }) {
  const { t } = useI18n()
  const copy = t.personalization
  const appearance = t.settings.appearance
  const { mode, setMode } = useTheme()
  const { requestGateway } = useGatewayRequest()
  const backdrop = useStore($backdrop)
  const imagePath = useStore($backdropImagePath)
  const opacity = useStore($backdropOpacity)
  const petEnabled = useStore($petInfo).enabled
  const previewSrc = useBackdropImageSrc(imagePath)

  const modeOptions = MODE_OPTIONS.map(({ id, icon }) => ({
    icon,
    id,
    label: t.settings.modeOptions[id].label
  }))

  const chooseImage = async () => {
    try {
      const [path] = await selectDesktopPaths({
        filters: [{ extensions: [...BACKDROP_IMAGE_EXTENSIONS], name: appearance.imageFilterName }],
        multiple: false,
        title: appearance.backdropTitle
      })

      if (!path) {
        return
      }

      setBackdropImagePath(path)
      setBackdrop(true)
      triggerHaptic('crisp')
    } catch (error) {
      notifyError(error, appearance.backdropTitle)
    }
  }

  const removeImage = () => {
    setBackdropImagePath(null)
    setBackdrop(false)
    triggerHaptic('selection')
  }

  const togglePet = (on: boolean) => {
    void setPetEnabled(requestGateway, on, {
      noneAvailable: appearance.pet.noneAvailable,
      fallback: on ? appearance.pet.turnOnFailed : appearance.pet.turnOffFailed
    }).then(ok => ok && triggerHaptic('crisp'))
  }

  return (
    <div className="flex flex-col gap-4" data-slot="personalization-panel">
      <header className="flex flex-col gap-0.5">
        <h2 className="text-sm font-semibold text-(--ui-text-primary)">{copy.title}</h2>
        <p className="text-xs leading-relaxed text-(--ui-text-tertiary)">{copy.subtitle}</p>
      </header>

      <section className="flex flex-col gap-2">
        <h3 className="text-[0.7rem] font-medium text-(--ui-text-tertiary)">{copy.themeTitle}</h3>
        <SegmentedControl
          onChange={id => {
            triggerHaptic('selection')
            setMode(id)
          }}
          options={modeOptions}
          value={mode}
        />
      </section>

      <section className="flex flex-col gap-2">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-[0.7rem] font-medium text-(--ui-text-tertiary)">{copy.backgroundTitle}</h3>
          {imagePath && (
            <Switch
              aria-label={copy.backgroundShow}
              checked={backdrop}
              onCheckedChange={on => setBackdrop(on)}
              size="xs"
            />
          )}
        </div>

        <div
          className="relative aspect-video w-full overflow-hidden rounded-md bg-(--ui-bg-quaternary)"
          data-slot="backdrop-preview"
        >
          {previewSrc ? (
            <img alt="" className="h-full w-full object-cover" src={previewSrc} style={{ opacity: opacity / 100 }} />
          ) : (
            <div className="grid h-full place-items-center px-3 text-center text-xs text-(--ui-text-tertiary)">
              {copy.noImage}
            </div>
          )}
        </div>

        <BackdropOpacitySlider label={copy.opacityLabel} />

        <div className="flex items-center gap-2">
          <Button onClick={() => void chooseImage()} size="sm" variant="secondary">
            {copy.chooseImage}
          </Button>
          {imagePath && (
            <Button onClick={removeImage} size="sm" variant="text">
              {copy.removeImage}
            </Button>
          )}
        </div>
      </section>

      <section className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-0.5">
          <h3 className="text-xs font-medium text-(--ui-text-primary)">{copy.petTitle}</h3>
          <p className="text-xs text-(--ui-text-tertiary)">{copy.petDesc}</p>
        </div>
        <Switch aria-label={copy.petTitle} checked={petEnabled} onCheckedChange={togglePet} size="xs" />
      </section>

      <Button className="self-start" onClick={onOpenAppearance} size="sm" variant="textStrong">
        {copy.moreSettings}
      </Button>
    </div>
  )
}
