import { useLayoutEffect } from 'react'

const pending = new Map<symbol, { label: string; scope?: string }>()

export function confirmLeave(scopes?: readonly string[]) {
  const labels = [...pending.values()]
    .filter(({ scope }) => !scopes || (scope && scopes.includes(scope)))
    .map(({ label }) => label)
  if (!labels.length) return true
  return window.confirm(`以下内容尚未保存或提交：${[...new Set(labels)].join('、')}。放弃这些修改并切换？`)
}

export function useLeaveGuard(dirty: boolean, label: string, scope?: string) {
  useLayoutEffect(() => {
    if (!dirty) return
    const id = Symbol(label)
    pending.set(id, { label, scope })
    const guard = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', guard)
    return () => {
      pending.delete(id)
      window.removeEventListener('beforeunload', guard)
    }
  }, [dirty, label, scope])
}
