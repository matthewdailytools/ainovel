import { expect, test } from '@playwright/test'

test('树内设定编辑保留未保存输入，并核对并发更新后显式采用', async ({ page }) => {
  await page.goto('/')
  const session = await (await page.request.get('/api/session')).json()
  const headers = { 'X-Ainovel-CSRF': session.csrf }
  const created = await page.request.post('/api/novels', {
    headers,
    data: { title: '后台验证 · 设定编辑冲突' },
  })
  expect(created.ok(), await created.text()).toBeTruthy()
  const novel = await created.json()
  const path = `/api/branches/${novel.branch_id}/definitions`
  async function readWorld() {
    const response = await page.request.get(path + '/world')
    expect(response.ok(), await response.text()).toBeTruthy()
    return response.json()
  }
  async function adoptWorld(text: string) {
    const current = await readWorld()
    const response = await page.request.post(path, {
      headers,
      data: { kind: 'world', text, base_story_revision: current.story_revision },
    })
    expect(response.ok(), await response.text()).toBeTruthy()
  }
  const original = '闸门在潮水退下后打开。'
  const local = '闸门在夜半退潮后打开。'
  const elsewhere = '验潮员确认退潮后才可以开闸。'
  await adoptWorld(original)
  await page.reload()
  await page.getByRole('combobox', { name: '作品', exact: true }).selectOption(novel.id)
  const tree = page.getByRole('navigation', { name: '作品树', exact: true })
  await tree.getByRole('button', { name: '世界设定', exact: true }).click()
  const center = page.getByRole('region', { name: '设定编辑', exact: true })
  const world = center.getByRole('textbox', { name: '世界设定正文', exact: true })
  await expect(world).toHaveValue(original)
  await world.fill(local)
  const confirmation = page.waitForEvent('dialog').then(async (dialog) => {
    expect(dialog.message()).toContain('世界设定草稿')
    await dialog.dismiss()
  })
  await Promise.all([confirmation, tree.getByRole('button', { name: '故事大纲', exact: true }).click()])
  await expect(world).toHaveValue(local)

  await adoptWorld(elsewhere)
  const save = center.getByRole('button', { name: '保存并采用', exact: true })
  await save.click()
  await expect(center.getByRole('heading', { name: '采用版本已在别处更新', exact: true })).toBeVisible()
  await expect(world).toHaveValue(local)
  await expect(save).toBeDisabled()
  await center.getByText('查看最新采用内容', { exact: true }).click()
  await expect(center.getByText(elsewhere, { exact: true })).toBeVisible()
  expect((await readWorld()).text).toBe(elsewhere)

  await center.getByRole('button', { name: '保留草稿，按最新版本继续编辑', exact: true }).click()
  await save.click()
  await expect(center.getByRole('status')).toHaveText('已保存并采用')
  expect((await readWorld()).text).toBe(local)
  await tree.getByRole('button', { name: '故事大纲', exact: true }).click()
  await expect(center.getByRole('heading', { name: '故事大纲', exact: true })).toBeVisible()
})
