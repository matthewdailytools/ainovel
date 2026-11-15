import { createServer } from 'node:http'
import { readFile } from 'node:fs/promises'

const root = new URL('../', import.meta.url)
// Serve only review artifacts; never expose configuration or manuscripts in this checkout.
const resources = new Map([
  ['/', ['novel-editor-prototype.html', 'text/html']],
  ['/novel-editor-prototype.html', ['novel-editor-prototype.html', 'text/html']],
  ['/prototypes/editor/editor.css', ['prototypes/editor/editor.css', 'text/css']],
  ['/prototypes/editor/editor.js', ['prototypes/editor/editor.js', 'text/javascript']],
  ['/21-小说编辑器调研与设计.md', ['21-小说编辑器调研与设计.md', 'text/plain']],
])
const port = Number(process.argv[2] || 8895)
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('端口应为1024—65535的整数。')
const server = createServer(async (request, response) => {
  try {
    const path = decodeURIComponent(new URL(request.url, 'http://localhost').pathname)
    const resource = resources.get(path)
    if (!resource || request.method !== 'GET') {
      response.writeHead(404)
      response.end()
      return
    }
    const content = await readFile(new URL(resource[0], root))
    response.writeHead(200, {
      'Content-Type': `${resource[1]}; charset=utf-8`,
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff',
    })
    response.end(content)
  } catch {
    response.writeHead(400)
    response.end('无法读取原型资源。')
  }
})
server.on('error', (error) => {
  console.error(`原型预览启动失败：${error.message}`)
  process.exitCode = 1
})
server.listen(port, '127.0.0.1', () =>
  console.log(`编辑器交互原型：http://127.0.0.1:${port}/novel-editor-prototype.html`),
)
