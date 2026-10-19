// @vitest-environment jsdom
import { cleanup, fireEvent, render } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SegmentedControl } from './segmented-control'

const options = [
  { id: 'a', label: 'First' },
  { id: 'b', label: 'Second' },
  { id: 'c', label: 'Third' }
] as const

function Controlled() {
  const [value, setValue] = useState<string>('a')
  return <SegmentedControl aria-label="View" onChange={setValue} options={options} value={value} />
}

afterEach(cleanup)

describe('SegmentedControl', () => {
  it('moves focus and selection synchronously with one tab stop', () => {
    const { getAllByRole } = render(<Controlled />)
    const [first, second, third] = getAllByRole('button')
    first.focus()
    fireEvent.keyDown(first, { key: 'ArrowRight' })
    expect(document.activeElement).toBe(second)
    expect(second.getAttribute('aria-pressed')).toBe('true')
    expect(first.tabIndex).toBe(-1)
    expect(second.tabIndex).toBe(0)
    fireEvent.keyDown(second, { key: 'End' })
    expect(document.activeElement).toBe(third)
    fireEvent.keyDown(third, { key: 'Home' })
    expect(document.activeElement).toBe(first)
  })

  it('does not claim modified arrows or unrelated keys', () => {
    const onChange = vi.fn()
    const { getAllByRole } = render(
      <SegmentedControl aria-label="Choices" onChange={onChange} options={options} value="a" />
    )
    fireEvent.keyDown(getAllByRole('button')[0], { key: 'ArrowRight', ctrlKey: true })
    fireEvent.keyDown(getAllByRole('button')[0], { key: 'Escape' })
    expect(onChange).not.toHaveBeenCalled()
  })

  it('keeps controlled state, empty options and disabled controls honest', () => {
    const onChange = vi.fn()
    const { container, getAllByRole, rerender } = render(
      <SegmentedControl aria-label="Choices" onChange={onChange} options={options} value="b" />
    )
    fireEvent.click(getAllByRole('button')[2])
    expect(onChange).toHaveBeenCalledWith('c')
    expect(getAllByRole('button')[1].getAttribute('aria-pressed')).toBe('true')
    rerender(<SegmentedControl aria-label="Choices" disabled onChange={onChange} options={options} value="b" />)
    expect(getAllByRole('button').every(button => (button as HTMLButtonElement).disabled)).toBe(true)
    rerender(<SegmentedControl aria-label="Choices" onChange={onChange} options={options} value="missing" />)
    expect(container.querySelector('[data-slot="segmented-control-indicator"]')).toBeNull()
    expect(getAllByRole('button')[0].tabIndex).toBe(0)
    rerender(<SegmentedControl aria-label="Choices" onChange={onChange} options={[]} value="missing" />)
    expect(container.querySelector('button')).toBeNull()
  })

  it('never shares its moving indicator with another control', () => {
    const { container } = render(
      <>
        <Controlled />
        <Controlled />
      </>
    )
    expect(container.querySelectorAll('[data-slot="segmented-control-indicator"]')).toHaveLength(2)
    const groups = container.querySelectorAll('[role="group"]')
    fireEvent.click(groups[0].querySelectorAll('button')[2])
    expect(groups[0].querySelectorAll('button')[2].getAttribute('aria-pressed')).toBe('true')
    expect(groups[1].querySelectorAll('button')[0].getAttribute('aria-pressed')).toBe('true')
  })

  it('uses computed RTL direction for real DOM arrow navigation and follows changing options', () => {
    const onChange = vi.fn()
    const { getAllByRole, rerender } = render(
      <div dir="rtl">
        <SegmentedControl aria-label="Choices" onChange={onChange} options={options} value="b" />
      </div>
    )
    const [, second] = getAllByRole('button')
    second.focus()
    fireEvent.keyDown(second, { key: 'ArrowRight' })
    expect(document.activeElement).toBe(getAllByRole('button')[0])
    expect(onChange).toHaveBeenLastCalledWith('a')
    rerender(
      <div dir="rtl">
        <SegmentedControl aria-label="Choices" onChange={onChange} options={options.slice(1)} value="missing" />
      </div>
    )
    expect(getAllByRole('button')[0].tabIndex).toBe(0)
  })
})
