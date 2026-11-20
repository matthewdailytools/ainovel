import { useAssistantTarget } from './WorkbenchAssistant'

type Target = Parameters<typeof useAssistantTarget>[0]

export function AssistantField({
  quickLabel = 'AI 润色',
  action = 'refine',
  discussionInDetails = false,
  ...target
}: Target & {
  quickLabel?: string
  action?: 'refine' | 'review' | 'suggest'
  discussionInDetails?: boolean
}) {
  const { openAssistant, targetEnabled } = useAssistantTarget(target)
  if (!targetEnabled) return null
  const discussion = (
    <button
      type="button"
      aria-label={`与 AI 讨论 · ${target.label}`}
      onClick={() => openAssistant('discuss')}
    >
      与 AI 讨论
    </button>
  )
  return (
    <div className="actions assistant-field-actions">
      <button
        type="button"
        aria-label={`${quickLabel} · ${target.label}`}
        onClick={() => openAssistant(action)}
      >
        {quickLabel}
      </button>
      {discussionInDetails ? (
        <details className="writing-assistant-discussion">
          <summary>更多</summary>
          {discussion}
        </details>
      ) : (
        discussion
      )}
    </div>
  )
}
