import { expect, test } from '@playwright/test'
import { createHash } from 'node:crypto'
import type { components } from '../../frontend/api-schema'
import { authorSettingPlan } from './fixtures/authorSettingPlan'

const cloud = process.env.AINOVEL_SETTING_USE_E2E_CLOUD ?? 'http://127.0.0.1:8888'
type Proof = components['schemas']['SettingUseVersion']

function digest(value: string) {
  return createHash('sha256').update(value).digest('hex')
}

function canonical(value: unknown) {
  return JSON.stringify(value, (_, part: unknown) =>
    part != null && typeof part === 'object' && !Array.isArray(part)
      ? Object.fromEntries(
          Object.keys(part)
            .sort()
            .map((key) => [key, (part as Record<string, unknown>)[key]]),
        )
      : part,
  )
}

test('桌面比较并发的设定正文证据，采用远端撤回后仍可回查本机证明', async ({ page }) => {
  test.skip(process.env.AINOVEL_SYNC_E2E !== '1', '此用例使用隔离的真实同步账本')
  await page.setViewportSize({ width: 390, height: 840 })
  const { novel, chapter, headers, original } = await authorSettingPlan(page, '同步比较设定正文证据')
  const branch = novel.branch_id
  const uses = `/api/chapters/${chapter.id}/setting-uses`
  const endpoint = `${cloud}/api/sync/works/${branch}`

  async function confirmProof(key: string, selection: { start: number; end: number }, parent?: string) {
    const { basis } = await (await page.request.get(uses)).json()
    const response = await page.request.post(uses, {
      headers,
      data: {
        outline_version_id: basis.outline_version_id,
        settings_version_id: basis.settings_version_id,
        item_id: 'keeper',
        parent_id: parent ?? null,
        chapter_sha256: basis.chapter_sha256,
        evidence: {
          chapter_id: chapter.id,
          revision_id: basis.chapter_revision_id,
          ...selection,
          quote_sha256: digest(original.slice(selection.start, selection.end)),
        },
        idempotency_key: `${chapter.id}-${key}`,
      },
    })
    expect(response.ok(), await response.text()).toBe(true)
    return (await response.json()).version as Proof
  }

  async function grant(device_id: string) {
    const response = await page.request.post(endpoint + '/devices', {
      headers: { Authorization: 'Bearer e2e-test-only-provisioning-token' },
      data: { device_id },
    })
    expect(response.ok(), await response.text()).toBe(true)
    return (await response.json()).token as string
  }

  const initial = await confirmProof('shared-setting-use', { start: 0, end: original.length })
  const desktopToken = await grant('setting-use-ui-desktop')
  const peerToken = await grant('setting-use-ui-peer')
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  await page.getByRole('button', { name: '故事与审阅', exact: true }).press('Enter')
  const panel = page.getByRole('region', { name: '作品云同步', exact: true })
  await panel.getByLabel('同步服务地址').fill(cloud)
  await panel.getByLabel('设备凭据').fill(desktopToken)
  const enable = panel.getByRole('button', { name: '启用作品同步', exact: true })
  await expect(enable).toBeEnabled()
  await enable.press('Enter')
  const sync = panel.getByRole('button', { name: '同步当前工作版本', exact: true })
  await expect(sync).toBeEnabled()
  await sync.press('Enter')
  await expect(panel.getByText(/已启用 · 游标 [1-9]\d* · 0 条待发送或确认/)).toBeVisible()

  const peerHeaders = { Authorization: `Bearer ${peerToken}` }
  const checkpoint = await page.request.get(endpoint + '/checkpoint', { headers: peerHeaders })
  expect(checkpoint.ok(), await checkpoint.text()).toBe(true)
  const ledgerHeader = { ...peerHeaders, 'X-Ainovel-Ledger': (await checkpoint.json()).ledger_id }
  const pageOfChanges = await page.request.get(endpoint + '/pull?after=0&limit=50', { headers: ledgerHeader })
  expect(pageOfChanges.ok(), await pageOfChanges.text()).toBe(true)
  const syncHead = (await pageOfChanges.json()).changes.find((row: { object_id: string }) =>
    row.object_id.startsWith('setting-use:'),
  )
  expect(syncHead.payload.revision_id).toBe(initial.id)
  const selected = '守铃人阿陶'
  const start = original.indexOf(selected)
  const local = await confirmProof('local-setting-use', { start, end: start + selected.length }, initial.id)
  const { id, ...known } = initial
  const operation = { ...known, parent_id: id, action: 'withdrawn' as const }
  const remote: Proof = { ...operation, id: digest(canonical([branch, operation])) }

  for (const body of [
    {
      change_id: 'peer-withdrawal-proof',
      object_id: `setting-use-version:${remote.id}`,
      parent_revision: null,
      payload: {
        format: 'ainovel-manuscript-sync/1',
        kind: 'setting-use-version',
        branch_id: branch,
        version: remote,
      },
    },
    {
      change_id: 'peer-withdrawal-head',
      object_id: syncHead.object_id,
      parent_revision: syncHead.head_revision,
      payload: { ...syncHead.payload, revision_id: remote.id },
    },
  ]) {
    const accepted = await page.request.post(endpoint + '/push', {
      headers: ledgerHeader,
      data: { ...body, tombstone: false },
    })
    expect(accepted.ok(), await accepted.text()).toBe(true)
    expect((await accepted.json()).status).toBe('applied')
  }

  await expect(sync).toBeEnabled()
  await sync.press('Enter')
  const compareButton = panel.getByRole('button', { name: /^比较设定正文证据 setting-use:/ })
  await expect(compareButton).toBeEnabled()
  const [compared] = await Promise.all([
    page.waitForResponse((response) => {
      const path = new URL(response.url()).pathname
      return (
        response.request().method() === 'POST' &&
        path.startsWith(`/api/branches/${branch}/desktop-sync/conflicts/`) &&
        path.endsWith('/compare')
      )
    }),
    compareButton.press('Enter'),
  ])
  expect(compared.ok(), await compared.text()).toBe(true)
  const compare = panel.getByRole('region', { name: '云同步冲突比较' })
  for (const [label, proof, text] of [
    ['本机当前版', local, '作者已确认正文使用'],
    ['云端当前版', remote, '已撤回正文使用证据'],
    ['共同依据', initial, '作者已确认正文使用'],
  ] as const) {
    const summary = compare.getByText(label, { exact: true })
    await summary.press('Enter')
    const details = summary.locator('..').locator('pre')
    await expect(details).toContainText(text)
    await expect(details).toContainText(`章节 ${chapter.id} · 设定条目 keeper`)
    await expect(details).toContainText(`章稿修订 ${proof.evidence.revision_id}`)
    await expect(details).toContainText(`原文选区 UTF-16 ${proof.evidence.start}–${proof.evidence.end}`)
    await expect(details).toContainText(`使用证明修订 ${proof.id}`)
    await expect(details).not.toContainText(selected)
  }
  const adoptCloud = compare.getByRole('button', { name: '采用云端当前版', exact: true })
  await expect(adoptCloud).toBeEnabled()
  page.once('dialog', (dialog) => void dialog.accept())
  await adoptCloud.press('Enter')
  await expect(compare).toHaveCount(0)
  await expect(panel.getByRole('button', { name: /^比较设定正文证据/ })).toHaveCount(0)
  const adopted = await (await page.request.get(`${uses}/${remote.id}`)).json()
  const historical = await (await page.request.get(`${uses}/${local.id}`)).json()
  expect(adopted.status).toBe('withdrawn')
  expect(historical).toMatchObject({ status: 'superseded', quote: selected })
})
