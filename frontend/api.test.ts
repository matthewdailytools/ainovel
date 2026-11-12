import { afterEach, expect, test, vi } from 'vitest'
import { api, startSession } from './api'

afterEach(() => vi.unstubAllGlobals())
test('writes carry only the backend session token, never a provider secret', async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ csrf: 'session-only', data_dir: 'local' })))
    .mockResolvedValueOnce(new Response('{}'))
  vi.stubGlobal('fetch', fetcher)
  await startSession()
  await api('/novels', 'POST', { title: '原创😀' })
  expect(fetcher.mock.calls[1][1].headers['X-Ainovel-CSRF']).toBe('session-only')
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ title: '原创😀' })
  expect(fetcher.mock.calls[1][1].credentials).toBe('same-origin')
})
test('conflict is exposed and cannot be presented as successful save', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ code: 'revision_conflict', message: '正文已有新版本' }), {
        status: 409,
      }),
    ),
  )
  await expect(api('/chapters/x', 'PUT', { content: '旧稿' })).rejects.toMatchObject({
    code: 'revision_conflict',
    status: 409,
    message: '正文已有新版本',
  })
})
