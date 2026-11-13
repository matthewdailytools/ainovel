import { test } from '@playwright/test'
import { manuscriptImportFlow } from './manuscript-import-flow'
test('长篇导入：预览不改稿、手动合并、跳过及回执', async ({ page }) => {
  await manuscriptImportFlow(page)
})
