const SHELL = new URL('./', self.location.href).href
const STATIC_PREFIX = 'ainovel-offline-shell-'
const HTML_CACHE = STATIC_PREFIX + 'pages-v1'
const FILE_CACHE = STATIC_PREFIX + 'files-v1'

function appAsset(value) {
  const url = new URL(value, SHELL)
  return url.origin === self.location.origin && /^\/assets\/[\w.-]+\.(?:m?js|css)$/.test(url.pathname)
}

async function refresh() {
  const response = await fetch(SHELL, { cache: 'no-cache', signal: AbortSignal.timeout(5000) })
  if (!response.ok || response.redirected) throw new Error('Offline shell unavailable')
  const html = await response.clone().text()
  const assets = [
    ...new Set(
      [...html.matchAll(/(?:src|href)="([^"]+)"/g)]
        .map((match) => match[1])
        .filter(appAsset)
        .map((path) => new URL(path, SHELL).href),
    ),
  ]
  if (!assets.length) throw new Error('Offline build assets unavailable')
  const fileCache = await caches.open(FILE_CACHE)
  const checks = await Promise.all(assets.map((asset) => fileCache.match(asset)))
  const missing = assets.filter((_, index) => !checks[index])
  if (missing.length) await fileCache.addAll(missing)
  const pageCache = await caches.open(HTML_CACHE)
  await pageCache.put(SHELL, response.clone())
  return response
}

self.addEventListener('install', (event) => {
  event.waitUntil(refresh().then(() => self.skipWaiting()))
})

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim())
})

self.addEventListener('fetch', (event) => {
  const { request } = event
  if (request.method !== 'GET') return
  const url = new URL(request.url)
  if (url.origin !== self.location.origin) return
  const shellUrl = new URL(SHELL)
  if (
    request.mode === 'navigate' &&
    [shellUrl.pathname, shellUrl.pathname + 'index.html'].includes(url.pathname)
  ) {
    event.respondWith(
      refresh().catch(async () => {
        const cache = await caches.open(HTML_CACHE)
        return (await cache.match(SHELL)) ?? Response.error()
      }),
    )
  } else if (appAsset(url.href)) {
    event.respondWith(
      caches.open(FILE_CACHE).then(async (cache) => (await cache.match(request)) ?? fetch(request)),
    )
  }
})
