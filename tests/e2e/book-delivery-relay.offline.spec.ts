import { expect, test, type APIResponse, type Page } from '@playwright/test'
import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { inflateRawSync } from 'node:zlib'

const first = '宁禾在闸门前找到一尺潮痕。😀\n她在册上留下两道待解释的刻痕。'
const checkQuote = '她复核旧读数，把结果落在册上。'
const endingQuote = '她公开了可核查的读数，暂缓修门的争执就此平息。'
const characterQuote = '宁禾把旧尺还给管事，两人约好各自保存一份记录。'
const promiseQuote = '两道刻痕合起来，正是昨天留下的水位读数。'
const second = [checkQuote, promiseQuote, characterQuote, endingQuote].join('\n')
const revisedSecond = second + '\n收起册子前，她又擦干了书页。'
const setting = '闸门只使用刻度尺，未取得维护批准时不能动手修门。'
const synopsis = '宁禾要说明闸门上的潮痕从何而来。'
const spoiler = '只使用第一章公开的铺垫，简介不揭示读数和修门争执的结果。'
const checkCondition = '第二章须复查刻度并保留记录，未执行修门。'
const firstOutline = '工程接力歧义：她检查闸门外露结构，记录读数，由作者明确是否修门。'
const secondOutline = '她检查闸门外露结构，复查并记录读数，不修门。'
const clarification = '她本章只检查闸门并记录，明确不修门。'
const labels = {
  manuscript: '稿件齐全与通读',
  ending: '主冲突与终局',
  characters: '关键人物与关系',
  promises: '伏笔与读者承诺',
  consistency: '一致性与复检',
  materials: '交付材料核验',
}
const notes = {
  manuscript: '已通读保存稿的两章，核对章节顺序、段落和承接。',
  ending: '核对报告公开后潮位争执正式平息。',
  characters: '核对宁禾归还旧尺并与管事约定分开存档。',
  promises: '两道刻痕对应前一日的读数，埋设与回收均已定位。',
  consistency: '检查了第二章关于复查读数和暂缓修门的结论。',
  materials: '已核对同一快照的正文、设定册、简介和第一章试读。',
}
const digest = (value: string | Buffer) => createHash('sha256').update(value).digest('hex')

async function read(response: APIResponse) {
  expect(response.ok(), await response.text()).toBeTruthy()
  return response.json()
}

function unpackZip(archive: Buffer) {
  // The exported ZIP is small. Read its central directory so both stored and deflated entries work.
  const footer = archive.lastIndexOf(Buffer.from('504b0506', 'hex'))
  expect(footer).toBeGreaterThanOrEqual(0)
  const count = archive.readUInt16LE(footer + 10)
  let offset = archive.readUInt32LE(footer + 16)
  const entries = new Map<string, Buffer>()
  for (let n = 0; n < count; n += 1) {
    expect(archive.readUInt32LE(offset)).toBe(0x02014b50)
    const method = archive.readUInt16LE(offset + 10)
    const compressed = archive.readUInt32LE(offset + 20)
    const nameLength = archive.readUInt16LE(offset + 28)
    const extra = archive.readUInt16LE(offset + 30)
    const comment = archive.readUInt16LE(offset + 32)
    const local = archive.readUInt32LE(offset + 42)
    const name = archive.toString('utf8', offset + 46, offset + 46 + nameLength)
    const start = local + 30 + archive.readUInt16LE(local + 26) + archive.readUInt16LE(local + 28)
    const bytes = archive.subarray(start, start + compressed)
    expect([0, 8]).toContain(method)
    entries.set(name, method === 0 ? bytes : inflateRawSync(bytes))
    offset += 46 + nameLength + extra + comment
  }
  return entries
}

async function boot(page: Page) {
  await page.goto('/')
  const { csrf } = await read(await page.request.get('/api/session'))
  const headers = { 'X-Ainovel-CSRF': csrf }
  const novel = await read(
    await page.request.post('/api/novels', { headers, data: { title: '后台验证 · 闸门潮痕与完本接力' } }),
  )
  const branch = `/api/branches/${novel.branch_id}`
  const chaptersUrl = branch + '/chapters'
  async function chapter(title: string, content: string, text: string) {
    const { id } = await read(await page.request.post(chaptersUrl, { headers, data: { title } }))
    const url = `/api/chapters/${id}`
    const saved = await read(await page.request.put(url, { headers, data: { base_revision: 0, content } }))
    const outline = await read(
      await page.request.put(chaptersUrl + `/${id}/writing-context/outline`, {
        headers,
        data: { base_version: null, text },
      }),
    )
    return { title, content, id, url, revision: saved.revision as number, outline }
  }
  const opening = await chapter('两道刻痕', first, firstOutline)
  const ending = await chapter('读数公开', second, secondOutline)
  const source = (row: typeof opening, quote: string) => {
    const start = row.content.indexOf(quote)
    expect(start).toBeGreaterThanOrEqual(0)
    return { chapter_id: row.id, revision: row.revision, start, end: start + quote.length, quote }
  }
  const world = await read(await page.request.get(branch + '/conception'))
  const savedWorld = await read(
    await page.request.put(branch + '/conception', {
      headers,
      data: {
        base_revision: world.revision,
        base_story_revision: world.story_revision,
        categories: world.categories.map((category: { id: string; items: unknown[] }) =>
          category.id === 'world'
            ? {
                ...category,
                items: [
                  ...category.items,
                  { id: 'floodgate-rule', title: '闸门维护规则', detail: setting, links: [] },
                ],
              }
            : category,
        ),
      },
    }),
  )
  await read(
    await page.request.post(branch + '/conception/confirm', {
      headers,
      data: {
        base_revision: savedWorld.revision,
        base_story_revision: savedWorld.story_revision,
        idempotency_key: crypto.randomUUID(),
        note: '作者确认闸门维护规则用于当前保存稿。',
      },
    }),
  )
  const graph = await read(await page.request.get(branch + '/story-graph'))
  const promiseGraph = await read(
    await page.request.post(branch + '/story-records', {
      headers,
      data: {
        base_story_revision: graph.story_revision,
        record: {
          kind: 'promise',
          title: '两道待解释的刻痕',
          stages: [
            { status: 'planted', sources: [source(opening, '她在册上留下两道待解释的刻痕。')] },
            { status: 'resolved', sources: [source(ending, promiseQuote)] },
          ],
        },
      },
    }),
  )
  const promiseId = promiseGraph.records.find((item: { kind: string }) => item.kind === 'promise').id
  const checkUrl = ending.url + '/checks'
  const checkTask = await read(
    await page.request.post(checkUrl, {
      headers,
      data: {
        base_revision: ending.revision,
        condition: checkCondition,
        account: 'offline',
        client_request_id: crypto.randomUUID(),
      },
    }),
  )
  async function task(id: string) {
    return read(await page.request.get(`/api/tasks/${id}`))
  }
  await expect.poll(async () => (await task(checkTask.id)).status).toBe('succeeded')
  const check = (await read(await page.request.get(checkUrl))).items[0]
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  return { novel, branch, opening, ending, promiseId, checkUrl, check, task, headers }
}

test('完本六项分别确认、未通过仍可预览原稿、旧新交付快照可回查', async ({ page }, info) => {
  const { branch, opening, ending, promiseId, checkUrl, check, task, headers } = await boot(page)
  const url = branch + '/delivery'
  const records = page.getByRole('button', { name: '故事与审阅', exact: true })
  const delivery = page.getByRole('region', { name: '完本验收与交付', exact: true })
  const selected = delivery.getByRole('region', { name: '所选交付包', exact: true })
  const gate = (title: string) => selected.getByRole('article', { name: title, exact: true })
  const confirm = selected.getByRole('button', { name: '确认完本', exact: true })
  const history = delivery.getByRole('combobox', { name: '历史交付包', exact: true })
  const quotes: Partial<Record<keyof typeof labels, string>> = {
    ending: endingQuote,
    characters: characterQuote,
    promises: promiseQuote,
    consistency: checkQuote,
  }
  await records.click()
  await page.getByText('完本验收与交付', { exact: true }).click()

  async function create(withEvidence: boolean) {
    await delivery.getByRole('button', { name: /^(准备交付材料|新建交付快照)$/ }).click()
    await delivery.getByRole('textbox', { name: '简介', exact: true }).fill(synopsis)
    await delivery.getByRole('textbox', { name: '剧透范围', exact: true }).fill(spoiler)
    await delivery.getByRole('combobox', { name: '试读起章', exact: true }).selectOption(opening.id)
    await delivery.getByRole('combobox', { name: '试读止章', exact: true }).selectOption(opening.id)
    if (withEvidence) {
      await delivery.getByText('填写六项核对依据', { exact: true }).click()
      for (const [key, title] of Object.entries(labels) as [keyof typeof labels, string][]) {
        await delivery.getByText(`填写${title}依据`, { exact: true }).click()
        await delivery.getByRole('textbox', { name: `${title}核对说明`, exact: true }).fill(notes[key])
        if (quotes[key]) {
          await delivery
            .getByRole('combobox', { name: `${title}引用章节`, exact: true })
            .selectOption(ending.id)
          await delivery.getByRole('textbox', { name: `${title}逐字引文`, exact: true }).fill(quotes[key]!)
        }
      }
    }
    const submitted = page.waitForResponse(
      (r) => r.url().endsWith(url + '/packages') && r.request().method() === 'POST',
    )
    await delivery.getByRole('button', { name: '创建交付快照', exact: true }).click()
    const response = await submitted
    return { value: await read(response), input: response.request().postDataJSON() }
  }

  async function download(label: string) {
    const event = page.waitForEvent('download')
    await delivery.getByRole('link', { name: label, exact: true }).click()
    const file = await (await event).path()
    if (!file) throw new Error('缺少本机下载文件。')
    return readFile(file)
  }

  async function finalize(id: string) {
    const formal = page.waitForResponse(
      (r) => r.url().endsWith(`/packages/${id}/finalize`) && r.request().method() === 'POST',
    )
    await confirm.click()
    return read(await formal)
  }

  async function checkArchive(
    value: {
      id: string
      files: { name: string; sha256: string; bytes: number }[]
      basis_sha256: string
      chapters: unknown[]
    },
    chapters: string,
    reportId: string,
  ) {
    const files = unpackZip(await download('下载完整交付包'))
    expect([...files.keys()].sort()).toEqual([
      'manifest.json',
      'manuscript.txt',
      'sample.txt',
      'settings.md',
      'synopsis.md',
    ])
    for (const file of value.files) {
      const bytes = files.get(file.name)!
      expect(bytes.length).toBe(file.bytes)
      expect(digest(bytes)).toBe(file.sha256)
    }
    expect(files.get('manuscript.txt')!.toString()).toContain(chapters)
    expect(files.get('settings.md')!.toString()).toContain(setting)
    expect(files.get('synopsis.md')!.toString()).toContain(synopsis)
    expect(files.get('sample.txt')!.toString()).toContain(first)
    expect(files.get('sample.txt')!.toString()).not.toContain(endingQuote)
    const manifest = JSON.parse(files.get('manifest.json')!.toString())
    expect(manifest).toMatchObject({
      package_id: value.id,
      basis_sha256: value.basis_sha256,
      chapters: value.chapters,
      sample_chapter_ids: [opening.id],
      spoiler_scope: spoiler,
      outline_versions: [
        { chapter_id: opening.id, version: opening.outline.version, source: 'author' },
        { chapter_id: ending.id, version: ending.outline.version, source: 'author' },
      ],
    })
    expect(manifest.ledger_revisions).toContainEqual(expect.objectContaining({ id: promiseId }))
    expect(manifest.check_report_ids).toContain(reportId)
    return files
  }

  const { value: pending } = await create(false)
  await expect(confirm).toBeDisabled()
  expect(pending.finished).toBeFalsy()
  expect(pending.gates.map((item: { label: string }) => item.label)).toEqual(Object.values(labels))
  await selected.getByText('预览交付材料', { exact: true }).click()
  for (const file of pending.files) {
    await selected.getByText(file.label, { exact: true }).click()
    await expect(selected.getByRole('link', { name: `下载${file.label}`, exact: true })).toBeVisible()
  }
  expect((await download('导出原稿')).toString()).toContain(second)
  const rejected = await page.request.post(`${url}/packages/${pending.id}/finalize`, {
    headers,
    data: { base_sha256: pending.basis_sha256, idempotency_key: crypto.randomUUID() },
  })
  expect(rejected.status(), await rejected.text()).toBe(409)

  const { value: ready, input: readyInput } = await create(true)
  expect(ready.status).toBe('ready')
  expect(ready.gates.map((item: { status: string }) => item.status)).toEqual(Array(6).fill('pass'))
  await expect(selected.getByRole('heading', { name: '六项已通过', exact: true })).toBeVisible()
  for (const [key, title] of Object.entries(labels) as [keyof typeof labels, string][]) {
    await gate(title).getByText(`${title} · 已通过`, { exact: true }).click()
    await expect(gate(title)).toContainText(notes[key])
    if (quotes[key]) {
      await expect(gate(title).getByText(quotes[key]!, { exact: true }).first()).toBeVisible()
      await expect(gate(title)).toContainText(`修订 ${ending.revision}`)
    }
  }
  expect(
    JSON.stringify(ready.gates.find((item: { id: string }) => item.id === 'consistency').evidence),
  ).toContain(check.latest.id)
  // Omit just one author confirmation at a time. Each gate must be independently blocking.
  for (const missing of Object.keys(labels)) {
    const incomplete = await read(
      await page.request.post(url + '/packages', {
        headers,
        data: {
          ...readyInput,
          idempotency_key: crypto.randomUUID(),
          notes: Object.fromEntries(Object.entries(notes).filter(([key]) => key !== missing)),
        },
      }),
    )
    expect(
      incomplete.gates
        .filter((item: { status: string }) => item.status !== 'pass')
        .map((item: { id: string }) => item.id),
    ).toEqual([missing])
  }
  const original = await finalize(ready.id)
  expect(original.status).toBe('finished')
  await expect(selected.getByRole('heading', { name: '已完结', exact: true })).toBeVisible()
  const oldFiles = await checkArchive(original, second, check.latest.id)

  await delivery.getByRole('button', { name: '新建交付快照', exact: true }).click()
  await delivery.getByText('填写六项核对依据', { exact: true }).click()
  await delivery.getByText('填写交付材料核验依据', { exact: true }).click()
  const materialsNote = delivery.getByRole('textbox', { name: '交付材料核验核对说明', exact: true })
  await expect(materialsNote).toHaveValue(notes.materials)
  await delivery.getByRole('textbox', { name: '简介', exact: true }).fill('修改后需要重核材料的简介。')
  await expect(materialsNote).toHaveValue('')
  await delivery.getByRole('button', { name: '关闭准备表单', exact: true }).click()

  await page.getByRole('button', { name: '写作', exact: true }).click()
  await page.locator('.chapter-list button').filter({ hasText: ending.title }).click()
  const editor = page.getByRole('textbox', { name: '章节正文', exact: true })
  await editor.fill(revisedSecond)
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect.poll(async () => (await read(await page.request.get(ending.url))).content).toBe(revisedSecond)
  const current = await read(await page.request.get(ending.url))
  await records.click()
  await page.getByText('完本验收与交付', { exact: true }).click()
  await history.selectOption(original.id)
  await expect(selected.getByRole('heading', { name: '历史快照', exact: true })).toBeVisible()
  expect(unpackZip(await download('下载完整交付包'))).toEqual(oldFiles)
  const { value: stale } = await create(true)
  expect(stale.gates.find((item: { id: string }) => item.id === 'consistency').status).not.toBe('pass')
  expect(stale.gates.find((item: { id: string }) => item.id === 'promises').status).not.toBe('pass')
  await expect(confirm).toBeDisabled()

  const ledgerUrl = branch + '/continuity-ledger'
  const ledger = await read(await page.request.get(ledgerUrl))
  const promise = ledger.entries.find((item: { id: string }) => item.id === promiseId)
  const payoffStart = revisedSecond.indexOf(promiseQuote)
  await read(
    await page.request.put(`${ledgerUrl}/entries/${promiseId}/evidence`, {
      headers,
      data: {
        base_revision: promise.revision,
        stage: 'resolved',
        source: {
          chapter_id: ending.id,
          revision: current.revision,
          start: payoffStart,
          end: payoffStart + promiseQuote.length,
          quote: promiseQuote,
        },
        note: '在新修订复核两道刻痕的回收。',
      },
    }),
  )
  const recheck = await read(
    await page.request.post(`${checkUrl}/${check.id}/recheck`, {
      headers,
      data: {
        base_revision: current.revision,
        previous_result_id: check.latest.id,
        account: 'offline',
        client_request_id: crypto.randomUUID(),
      },
    }),
  )
  await expect.poll(async () => (await task(recheck.id)).status).toBe('succeeded')
  const latestCheck = (await read(await page.request.get(checkUrl))).items[0]
  expect(latestCheck.history).toContainEqual(check.latest)
  await delivery.getByRole('button', { name: '刷新交付依据', exact: true }).click()
  const { value: fresh } = await create(true)
  expect(fresh.basis_sha256).not.toBe(original.basis_sha256)
  expect(fresh.chapters).toContainEqual(
    expect.objectContaining({ id: ending.id, revision: current.revision, sha256: digest(revisedSecond) }),
  )
  expect(fresh.gates.every((item: { status: string }) => item.status === 'pass')).toBeTruthy()
  const newPackage = await finalize(fresh.id)
  const newFiles = await checkArchive(newPackage, revisedSecond, latestCheck.latest.id)
  expect(newFiles.get('manuscript.txt')).not.toEqual(oldFiles.get('manuscript.txt'))
  expect((await read(await page.request.get(branch + '/finalization'))).author_finalized).toBeFalsy()
  await history.selectOption(original.id)
  await expect(selected.getByRole('heading', { name: '历史快照', exact: true })).toBeVisible()
  expect(unpackZip(await download('下载完整交付包'))).toEqual(oldFiles)
  await page.setViewportSize({ width: 320, height: 800 })
  await page.evaluate(() => {
    document.documentElement.style.fontSize = '200%'
  })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy()
  await delivery.screenshot({ path: info.outputPath('book-delivery-320-200.png') })
})

test('已确认章纲接力：预算停止、换账户保留范围、作者补充后完成', async ({ page }) => {
  const { branch, opening, ending, task } = await boot(page)
  await page.getByRole('button', { name: '整书创作', exact: true }).click()
  await page.getByRole('checkbox', { name: '从已确认章纲接力', exact: true }).check()
  const scope = page.getByRole('region', { name: '接力章纲范围', exact: true })
  await scope.getByRole('combobox', { name: '起始章', exact: true }).selectOption(opening.id)
  await scope.getByRole('combobox', { name: '结束章', exact: true }).selectOption(ending.id)
  await scope.getByText('核对接力范围（2 章）', { exact: true }).click()
  await scope.getByText(opening.title, { exact: true }).click()
  await expect(scope.getByText(firstOutline, { exact: true })).toBeVisible()
  await page.getByRole('combobox', { name: '本次任务范围', exact: true }).selectOption('segment')
  await page.getByText('来源与固定条件', { exact: true }).click()
  await page
    .getByRole('textbox', { name: '作品设定、粗纲与结局方向', exact: true })
    .fill('宁禾检查闸门并记录读数；本批仅验证两章接力。')
  await page.getByRole('spinbutton', { name: '每章字数下限', exact: false }).fill('200')
  await page.getByRole('spinbutton', { name: '每章字数上限', exact: false }).fill('600')
  await page.getByRole('combobox', { name: '采纳政策', exact: true }).selectOption('automatic')
  await page.getByText('预算与执行说明', { exact: true }).click()
  await page.getByRole('spinbutton', { name: '本批次预算（可低于共享总上限）', exact: true }).fill('0.01')
  const startedResponse = page.waitForResponse(
    (r) => r.url().endsWith('/api/workflows') && r.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '创建独立版本并启动', exact: true }).click()
  const started = await read(await startedResponse)
  const resultBranch = `/api/branches/${started.branch_id}`
  expect(resultBranch).not.toBe(branch)
  expect(started.payload.outline_sources).toEqual([
    { chapter_id: opening.id, version: opening.outline.version },
    { chapter_id: ending.id, version: ending.outline.version },
  ])
  const frozen = started.result.outline_relay
  expect(frozen.sources.map((source: { outline_sha256: string }) => source.outline_sha256)).toEqual([
    digest(firstOutline),
    digest(secondOutline),
  ])
  async function noGeneratedProse() {
    const rows = await read(await page.request.get(resultBranch + '/chapters'))
    for (const row of rows) {
      const chapter = await read(await page.request.get(`/api/chapters/${row.id}`))
      expect(chapter.content).toBe('')
    }
    expect(await read(await page.request.get(`/api/artifacts?branch_id=${started.branch_id}`))).toEqual([])
  }
  await expect.poll(async () => (await task(started.id)).status).toBe('blocked_runtime')
  const blocked = await task(started.id)
  expect(blocked.error).toContain('预算不足')
  expect(blocked.result.next_chapter).toBe(1)
  await noGeneratedProse()
  await expect(page.getByText(/本次任务预算不足/)).toBeVisible()
  const budgetAcknowledgement = page.getByRole('checkbox', {
    name: '已查看费用记录，明确同意恢复时可能产生新调用',
    exact: true,
  })
  if (await budgetAcknowledgement.isVisible()) await budgetAcknowledgement.check()
  await page.getByText('换账户，从检查点续跑', { exact: true }).click()
  await page.getByRole('combobox', { name: '后续执行账户', exact: true }).selectOption('offline-relay')
  await page
    .getByRole('textbox', { name: '换账户原因', exact: true })
    .fill('本批子预算耗尽，沿用原章纲与检查点续跑。')
  await page.getByRole('spinbutton', { name: /^续跑预算上限/ }).fill('1')
  const continuationResponse = page.waitForResponse(
    (r) => r.url().endsWith(`/api/tasks/${started.id}/continue`) && r.request().method() === 'POST',
  )
  await page.getByRole('button', { name: '使用所选账户续跑', exact: true }).click()
  const continuation = await read(await continuationResponse)
  expect(continuation.branch_id).toBe(started.branch_id)
  expect(continuation.result.outline_relay).toEqual(frozen)
  expect(continuation.result.continued_from).toBe(started.id)
  await expect.poll(async () => (await task(continuation.id)).status).toBe('needs_review')
  const waiting = await task(continuation.id)
  const checkpoint = waiting.result.outline_relay_waiting
  expect(checkpoint.number).toBe(1)
  expect(checkpoint.contract.items[0].certainty).toBe('ambiguous')
  expect(waiting.result.waiting_artifact).toBeUndefined()
  await noGeneratedProse()
  await expect(page.getByRole('button', { name: '从保存的检查点恢复', exact: true })).toBeDisabled()
  const author = page.getByRole('form', { name: '章纲接力待作者决定', exact: true })
  await expect(author.getByRole('heading', { name: '第 1 章待作者决定', exact: true })).toBeVisible()
  await expect(author).toContainText('已停在正文生成之前')
  await author.getByRole('textbox', { name: '作者明确补充', exact: true }).fill(clarification)
  const clarifiedResponse = page.waitForResponse(
    (r) =>
      r.url().endsWith(`/api/tasks/${continuation.id}/outline-relay/clarify`) &&
      r.request().method() === 'POST',
  )
  await author.getByRole('button', { name: '采用补充并继续接力', exact: true }).click()
  const released = await read(await clarifiedResponse)
  expect(released.id).toBe(continuation.id)
  expect(released.payload.budget_limit).toBe(1)
  expect(released.result.outline_relay).toEqual(frozen)
  expect(released.result.outline_relay_clarifications[0]).toMatchObject({
    previous_preparation: { id: checkpoint.id },
    request: { checkpoint_id: checkpoint.id, clarification },
  })
  await expect.poll(async () => (await task(continuation.id)).status).toBe('segment_complete')
  const final = await task(continuation.id)
  expect(final.result.outline_relay_waiting).toBeUndefined()
  expect(final.result.plan.chapters[0].plot).toBe(`${firstOutline}\n\n作者明确补充：${clarification}`)
  expect(final.result.plan.chapters[1].plot).toBe(secondOutline)
  expect((await task(started.id)).status).toBe('continued')
  await expect(page.getByText('本批阶段完成，非全书完结', { exact: true })).toBeVisible()
  const generated = await read(await page.request.get(resultBranch + '/chapters'))
  expect(generated).toHaveLength(2)
  for (const chapter of generated) {
    const saved = await read(await page.request.get(`/api/chapters/${chapter.id}`))
    expect(saved.revision).toBeGreaterThan(0)
    expect([...saved.content.trim()].length).toBeGreaterThanOrEqual(200)
  }
  for (const chapter of [opening, ending]) {
    expect((await read(await page.request.get(chapter.url))).content).toBe(chapter.content)
    const outline = await read(await page.request.get(`${branch}/chapters/${chapter.id}/writing-context`))
    expect(outline.outline).toEqual(chapter.outline)
  }
})
