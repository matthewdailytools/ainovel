import type { KeyboardEvent } from 'react'

export function WorkbenchTabs<T extends string>({
  id,
  label,
  items,
  value,
  onChange,
  className,
}: {
  id: string
  label: string
  items: readonly (readonly [T, string])[]
  value: T
  onChange: (value: T) => void
  className?: string
}) {
  function handleKey(event: KeyboardEvent<HTMLDivElement>) {
    if (event.nativeEvent.isComposing || event.altKey || event.metaKey || event.ctrlKey) return
    const index = items.findIndex(([name]) => name === value)
    const destination = {
      ArrowLeft: (index + items.length - 1) % items.length,
      ArrowRight: (index + 1) % items.length,
      Home: 0,
      End: items.length - 1,
    }[event.key]
    if (destination === undefined) return
    const target = items[destination]?.[0]
    if (target === undefined) return
    event.preventDefault()
    document.getElementById(id + '-' + target)?.focus()
    onChange(target)
  }

  return (
    <div className={className} role="tablist" aria-label={label} onKeyDown={handleKey}>
      {items.map(([name, title]) => (
        <button
          key={name}
          id={id + '-' + name}
          type="button"
          role="tab"
          aria-controls={id + '-panel-' + name}
          aria-selected={value === name}
          tabIndex={value === name ? 0 : -1}
          onClick={(event) => {
            event.currentTarget.focus()
            onChange(name)
          }}
        >
          {title}
        </button>
      ))}
    </div>
  )
}
