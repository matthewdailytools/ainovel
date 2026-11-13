import { useLayoutEffect } from 'react'

const pending = new Map<symbol, string>()

export function confirmLeave() {
  if (!pending.size) return true
  return window.confirm(
    `以下内容尚未保存或提交：${[...new Set(pending.values())].join('、')}。放弃这些修改并切换？`,
  )
}

export function useLeaveGuard(dirty: boolean, label: string) {
  useLayoutEffect(() => {
    if (!dirty) return
    const id = Symbol(label)
    pending.set(id, label)
    const guard = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', guard)
    return () => {
      pending.delete(id)
      window.removeEventListener('beforeunload', guard)
    }
  }, [dirty, label])
}
