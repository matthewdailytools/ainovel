export type FeedbackHandoff = {
  id: string
  branch_id: string
  chapter_id: string
  title: string
  source_revision: number
  category: string
  text: string
  source_quote: string
}

export function feedbackRevisionInstruction(feedback: FeedbackHandoff, authorRequest: string) {
  return `依据作者主动选择的手动反馈生成局部修订候选。此反馈是作者或读者意见，尚未由独立模型审稿证实；不自动将“事实错误”分类视为已证实事实，不将审美偏好升级成作品约束。保留作者原定情节、结果及选区外正文。
反馈来源：${JSON.stringify({ id: feedback.id, branch_id: feedback.branch_id, chapter_id: feedback.chapter_id, source_revision: feedback.source_revision, category: feedback.category })}
历史反馈原文引用（仅为原反馈快照，可能已过期，不是本次选区）：${feedback.source_quote || '未提供'}
原反馈意见：${feedback.text}
作者本次修订要求：${authorRequest}`
}

export function checkFeedbackTarget(
  feedback: FeedbackHandoff,
  target: { branchId: string; chapterId: string; revision: number; confirmedRevision: number | null },
) {
  if (target.branchId !== feedback.branch_id || target.chapterId !== feedback.chapter_id)
    throw new Error('请回到该反馈对应的章节，或退出反馈修订后继续其他操作。')
  if (target.revision !== feedback.source_revision && target.confirmedRevision !== target.revision)
    throw new Error('正文与反馈来源修订不同，请先核对当前保存稿、确认新范围后再生成。')
}
