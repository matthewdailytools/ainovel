import { expect, test } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { writeFile } from 'node:fs/promises'

const cloud = 'http://127.0.0.1:8888'
test.skip(process.env.AINOVEL_SYNC_E2E !== '1', '使用隔离云端进程验收大体积交付')

test('长篇交付只同步冻结清单和来源，空白 Web 恢复后 ZIP 四份材料逐件一致', async ({ page }, testInfo) => {
  await page.goto('/')
  const { csrf } = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': csrf }
  const newWork = await page.request.post('/api/novels', { headers, data: { title: '长篇交付隔离验收' } })
  expect(newWork.ok()).toBe(true)
  const { branch_id } = await newWork.json()
  const branch = `/api/branches/${branch_id}`
  const lead = '铜铃的来历已记录。'
  let first: { id: string; revision: number } | undefined
  const text = lead + '学徒依据铜铃上的刻痕找到了师姐。'.repeat(11000)
  expect(Buffer.byteLength(text) * 2).toBeGreaterThan(1024 * 1024)
  for (const title of ['拾铃', '归铃']) {
    const created = await page.request.post(`${branch}/chapters`, { headers, data: { title } })
    expect(created.ok()).toBe(true)
    const { id } = await created.json()
    const saved = await page.request.put(`/api/chapters/${id}`, {
      headers,
      data: { base_revision: 0, content: text },
    })
    expect(saved.ok(), await saved.text()).toBe(true)
    first ??= await saved.json()
  }
  const response = await page.request.get(`${branch}/workflow-inspections`)
  const { preview } = await response.json()
  const inspection = await page.request.post(`${branch}/workflow-inspections`, {
    headers,
    data: {
      basis_sha256: preview.basis_sha256,
      length_targets: { min_chars: 1, expected_chapters: 2 },
      author_reviews: preview.items
        .filter(({ status }: { status: string }) => status === 'pending')
        .map(({ check_id }: { check_id: string }) => ({
          check_id,
          status: 'green',
          basis_note: '已读当前原稿。',
          reason: '当前故事已闭合。',
          evidence: [
            { chapter_id: first!.id, revision: first!.revision, start: 0, end: lead.length, quote: lead },
          ],
        })),
    },
  })
  expect(inspection.ok(), await inspection.text()).toBe(true)
  const { basis_sha256 } = await (await page.request.get(`${branch}/book-completion`)).json()
  const completion = await page.request.post(`${branch}/book-completion/confirm`, {
    headers,
    data: {
      basis_sha256,
      read_note: '已校对两章原稿。',
      ending: { note: '铜铃归还。' },
      characters: { note: '师姐与主角离开渡口。' },
      promises: { note: '原有承诺均处理。' },
      intro: '一枚铜铃让两名故人重逢。',
      spoiler_scope: 'opening',
      sample_chapter_ids: [first!.id],
      materials_reviewed: true,
    },
  })
  expect(completion.ok(), await completion.text()).toBe(true)
  const { id: delivery } = await completion.json()
  const original = await page.request.get(`${branch}/book-completion/${delivery}/export`)
  expect(original.ok()).toBe(true)
  const originalFile = testInfo.outputPath('large-desktop.zip')
  await writeFile(originalFile, await original.body())

  async function grant(device_id: string) {
    const device = await page.request.post(`${cloud}/api/sync/works/${branch_id}/devices`, {
      headers: { Authorization: 'Bearer e2e-test-only-provisioning-token' },
      data: { device_id },
    })
    expect(device.ok()).toBe(true)
    return (await device.json()).token as string
  }
  const native = await grant('desktop-large-workflow')
  const web = await grant('web-large-workflow')
  for (const action of ['enable', 'run']) {
    const synced = await page.request.post(`${branch}/desktop-sync/${action}`, {
      headers,
      data: { origin: cloud, token: native },
    })
    expect(synced.ok(), await synced.text()).toBe(true)
  }
  const pull = await page.request.get(`${cloud}/api/sync/works/${branch_id}/pull?after=0&limit=100`, {
    headers: { Authorization: `Bearer ${web}` },
  })
  expect(pull.ok()).toBe(true)
  const { changes } = await pull.json()
  const event = changes.find(
    (row: { object_id: string }) => row.object_id === `workflow-completion:${delivery}`,
  )
  expect(event).toBeTruthy()
  expect(Buffer.byteLength(event.payload.entry_json)).toBeLessThan(1024 * 1024)
  expect(JSON.parse(event.payload.entry_json)).not.toHaveProperty('materials')
  const packageFile = await page.request.get(`${branch}/manuscript-package`)
  expect(packageFile.ok()).toBe(true)
  const { format, novel, branch: branchInfo } = await packageFile.json()
  await page.goto('/offline/')
  await page.getByLabel('导入新离线作品').setInputFiles({
    name: '仅作品标识.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify({ format, novel, branch: branchInfo, chapters: [], revisions: [] })),
  })
  await page.getByText('作品云同步', { exact: true }).click()
  await page.getByLabel('同步服务地址').fill(cloud)
  await page.getByLabel('设备编号').fill('web-large-workflow')
  await page.getByLabel('设备凭据').fill(web)
  await page.getByRole('button', { name: '启用作品同步', exact: true }).click()
  await page.getByRole('button', { name: '同步当前工作版本', exact: true }).click()
  const archive = page.getByLabel('历次体检与完本交付')
  await archive.getByText('历次体检与完本交付', { exact: true }).click()
  await archive.getByLabel('选择历史报告').selectOption(delivery)
  const transfer = page.waitForEvent('download')
  await archive.getByRole('button', { name: '下载历史交付 ZIP' }).click()
  const downloaded = testInfo.outputPath('large-web.zip')
  await (await transfer).saveAs(downloaded)
  execFileSync(process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python', [
    '-c',
    [
      'import json, sys, zipfile',
      'with zipfile.ZipFile(sys.argv[1]) as source, zipfile.ZipFile(sys.argv[2]) as received:',
      '    assert received.testzip() is None and set(source.namelist()) == set(received.namelist())',
      '    assert source.getinfo("正文.md").file_size > 1024 * 1024',
      '    assert json.loads(source.read("manifest.json")) == json.loads(received.read("manifest.json"))',
      '    assert all(source.read(name) == received.read(name) for name in source.namelist() if name != "manifest.json")',
    ].join('\n'),
    originalFile,
    downloaded,
  ])
})
