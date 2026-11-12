import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { resolve } from 'node:path'
import { readFileSync, writeFileSync } from 'node:fs'
import openapiTS, { astToString } from 'openapi-typescript'
import { format } from 'prettier'

const root = fileURLToPath(new URL('..', import.meta.url))
const python =
  process.platform === 'win32' ? resolve(root, '.venv/Scripts/python.exe') : resolve(root, '.venv/bin/python')
const result = spawnSync(python, ['-m', 'ainovel', 'schema', '--file', 'frontend/openapi.json'], {
  cwd: root,
  encoding: 'utf8',
})
if (result.status !== 0) throw new Error(result.stderr || 'OpenAPI 导出失败')
const schema = JSON.parse(readFileSync(resolve(root, 'frontend/openapi.json'), 'utf8'))
writeFileSync(
  resolve(root, 'frontend/openapi.json'),
  await format(JSON.stringify(schema), { parser: 'json', printWidth: 110 }),
  'utf8',
)
const ast = await openapiTS(schema)
writeFileSync(
  resolve(root, 'frontend/api-schema.ts'),
  await format(astToString(ast), { parser: 'typescript', singleQuote: true, semi: false, printWidth: 110 }),
  'utf8',
)
