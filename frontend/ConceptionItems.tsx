import { useState } from 'react'
import type { components } from './api-schema'
import { settingAdoptionLabels, type SettingAdoptionState } from './conception'
import type {
  CandidateCategory,
  ConceptionCategory,
  ConceptionIssue,
  ConceptionItem,
  ConceptionModelReview,
  ConceptionSource,
} from './conception'

export type SettingFinding = components['schemas']['SettingFinding']
export type SettingInspection = components['schemas']['SettingInspection']

const findingLabels: Record<SettingFinding['kind'], string> = {
  same_name: '同名条目的内容或约束不同',
  missing_link: '关联目标不存在',
  self_link: '项目关联自身',
  duplicate_link: '同一目标重复关联',
  skipped_link: '关联目标所在类别已暂缓',
  empty_link: '关联目标尚无设定内容',
  policy_link: '关联规则的适用范围或例外文字不同',
}
const invalidLinks: SettingFinding['kind'][] = ['missing_link', 'self_link', 'duplicate_link']

export function ConceptionSettingFindings({
  findings,
  categories,
  checked,
  busy,
  onLocate,
  onReview,
  onPrefer,
}: {
  findings: SettingFinding[]
  categories: ConceptionCategory[]
  checked: string[]
  busy: boolean
  onLocate: (categoryId: string, itemId: string) => void
  onReview?: (id: string, selected: boolean) => void
  onPrefer?: (finding: SettingFinding, itemId: string) => void
}) {
  const items = new Map(categories.flatMap((category) => category.items.map((item) => [item.id, item])))
  return (
    <ul className="conception-issues" style={{ overflowWrap: 'anywhere' }}>
      {findings.map((finding) => {
        const reviewed = finding.reviewed || checked.includes(finding.id)
        const mustFix = invalidLinks.includes(finding.kind)
        const preferredChoice =
          finding.kind === 'same_name' || finding.kind === 'policy_link' ? onPrefer : undefined
        return (
          <li key={finding.id}>
            <details>
              <summary>
                {findingLabels[finding.kind]} · {mustFix ? '需修复' : reviewed ? '已核对' : '待核对'}
              </summary>
              <ol>
                {finding.locations.map((location, index) => {
                  const item = items.get(location.item_id)
                  const policy = item?.setting_policy
                  const categoryId = location.category_id
                  return (
                    <li key={`${location.item_id}-${index}`}>
                      <p className="small">
                        {location.category_title
                          ? `${location.category_title}（${categoryId}） · 第 ${location.item_number} 项`
                          : '目标位置未找到'}
                        {location.link_number ? ` · 第 ${location.link_number} 个关联` : ''}
                        {' · 条目编号 '}
                        {location.item_id}
                      </p>
                      {item && categoryId && (
                        <>
                          <button type="button" onClick={() => onLocate(categoryId, item.id)}>
                            定位「{item.title}」 · {location.item_number}
                          </button>
                          {item.detail && <p className="small">{item.detail}</p>}
                          {policy && (
                            <p className="small">
                              {policy.strength === 'hard' ? '硬规定' : '可调整设定'}
                              {' · 适用范围 '}
                              {policy.scope || '未填写'}
                              {' · 例外 '}
                              {policy.exceptions || '未填写'}
                            </p>
                          )}
                          {preferredChoice && (
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => preferredChoice(finding, item.id)}
                            >
                              采用{location.category_title}第 {location.item_number} 项，其余待定
                            </button>
                          )}
                        </>
                      )}
                    </li>
                  )
                })}
              </ol>
              {preferredChoice && (
                <p className="small">取舍先存为草稿。也可定位条目，调整各自的适用范围或例外后重新体检。</p>
              )}
              {finding.kind === 'skipped_link' && <p className="small">暂缓的类别不会随当前构思交接。</p>}
              {onReview && !mustFix && (
                <label>
                  <input
                    type="checkbox"
                    checked={reviewed}
                    disabled={busy || finding.reviewed}
                    onChange={(event) => onReview(finding.id, event.target.checked)}
                  />
                  已核对此项，按当前内容保留
                </label>
              )}
            </details>
          </li>
        )
      })}
    </ul>
  )
}

export function ConceptionQuote({
  source,
  current = true,
  edited = false,
}: {
  source: ConceptionSource
  current?: boolean
  edited?: boolean
}) {
  return (
    <details className="conception-quote">
      <summary>查看原文依据{current ? '' : '（历史来源）'}</summary>
      <p className="small">
        章节 {source.chapter_id.slice(0, 8)} · 修订 {source.revision} · 原文范围 {source.start}–{source.end}
      </p>
      <blockquote>{source.quote}</blockquote>
      {!current && <p className="small muted">保存稿已更新，请核对这条设定是否仍适用。</p>}
      {edited && <p className="small muted">设定含作者编辑。此引文对应最初的 AI 提议。</p>}
    </details>
  )
}

export function ConceptionItemEditor({
  item,
  choices,
  worldSetting,
  onChange,
}: {
  item: ConceptionItem
  choices: ConceptionItem[]
  worldSetting?: boolean
  onChange: (item: ConceptionItem) => void
}) {
  const [target, setTarget] = useState('')
  const links = item.links || []
  const available = choices.filter((choice) => choice.id !== item.id && !links.includes(choice.id))
  return (
    <>
      <label>
        项目名称
        <input
          value={item.title}
          maxLength={200}
          onChange={(event) => onChange({ ...item, title: event.target.value })}
        />
      </label>
      <label>
        采用状态
        <select
          value={item.adoption_state ?? 'adopted'}
          onChange={(event) =>
            onChange({ ...item, adoption_state: event.target.value as SettingAdoptionState })
          }
        >
          <option value="pending">{settingAdoptionLabels.pending}</option>
          <option value="proposed">{settingAdoptionLabels.proposed}</option>
          <option value="adopted">随本版确认采用</option>
        </select>
      </label>
      {worldSetting && (
        <fieldset>
          <legend>规则约束</legend>
          <label>
            约束强度
            <select
              value={item.setting_policy?.strength ?? ''}
              onChange={(event) => {
                const value = event.target.value
                onChange({
                  ...item,
                  setting_policy:
                    value === 'hard' || value === 'soft'
                      ? { scope: '', exceptions: '', ...item.setting_policy, strength: value }
                      : null,
                })
              }}
            >
              <option value="">未指定强度</option>
              <option value="hard">硬规定</option>
              <option value="soft">可调整设定</option>
            </select>
          </label>
          {item.setting_policy &&
            (['scope', 'exceptions'] as const).map((key) => (
              <label key={key}>
                {key === 'scope' ? '适用范围' : '例外'}
                <textarea
                  rows={2}
                  maxLength={800}
                  value={item.setting_policy?.[key] ?? ''}
                  onChange={(event) => {
                    if (item.setting_policy)
                      onChange({
                        ...item,
                        setting_policy: { ...item.setting_policy, [key]: event.target.value },
                      })
                  }}
                />
              </label>
            ))}
        </fieldset>
      )}
      <label>
        设定内容
        <textarea
          rows={3}
          maxLength={4000}
          value={item.detail}
          onChange={(event) => onChange({ ...item, detail: event.target.value })}
        />
      </label>
      {(!!available.length || !!links.length) && (
        <details>
          <summary>关联项目 · {links.length} 项</summary>
          {!!links.length && (
            <ul className="conception-links">
              {links.map((id) => (
                <li key={id}>
                  <span>{choices.find((choice) => choice.id === id)?.title || '待核对的关联'}</span>
                  <button
                    type="button"
                    onClick={() => onChange({ ...item, links: links.filter((link) => link !== id) })}
                    aria-label={`移除关联 ${choices.find((choice) => choice.id === id)?.title || id}`}
                  >
                    移除关联
                  </button>
                </li>
              ))}
            </ul>
          )}
          {!!available.length && links.length < 30 && (
            <div className="actions">
              <label>
                选择关联项目
                <select value={target} onChange={(event) => setTarget(event.target.value)}>
                  <option value="">请选择</option>
                  {available.map((choice) => (
                    <option key={choice.id} value={choice.id}>
                      {choice.title || '未命名项目'}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                disabled={!available.some((choice) => choice.id === target)}
                onClick={() => {
                  onChange({ ...item, links: [...links, target] })
                  setTarget('')
                }}
              >
                添加关联
              </button>
            </div>
          )}
        </details>
      )}
    </>
  )
}

export function ConceptionCandidateReview({
  categories,
  candidates,
  busy,
  stale,
  onAdopt,
  evidence,
  issues,
  modelReview,
  rejected,
  onReject,
}: {
  categories: ConceptionCategory[]
  candidates: CandidateCategory[]
  busy: boolean
  stale: boolean
  onAdopt: (categories: CandidateCategory[]) => void
  evidence?: Record<string, ConceptionSource>
  issues: ConceptionIssue[]
  modelReview?: ConceptionModelReview
  rejected: boolean
  onReject?: () => void
}) {
  const [edits, setEdits] = useState(candidates)
  const [chosen, setChosen] = useState<string[]>([])
  const currentItems = categories.flatMap((category) => category.items)
  const originalItems = new Map(
    candidates.flatMap((category) => category.items.map((item) => [item.id, item])),
  )
  const currentIds = new Set(currentItems.map((item) => item.id))
  const chosenIds = new Set([...currentIds, ...chosen])
  const choices = [...currentItems, ...edits.flatMap((category) => category.items)].filter(
    (item, index, all) => all.findIndex((candidate) => candidate.id === item.id) === index,
  )
  const selected = edits
    .map((category) => ({
      ...category,
      items: category.items.filter((item) => chosen.includes(item.id)),
    }))
    .filter((category) => category.items.length)
  const missingLink = selected.some((category) =>
    category.items.some((item) => item.links?.some((link) => !chosenIds.has(link))),
  )
  return (
    <section className="conception-review" aria-label="核对 AI 草案">
      <h3>核对 AI 草案</h3>
      {rejected ? (
        <p className="notice">已拒绝 · 历史草案</p>
      ) : (
        <p>勾选并编辑需要采用的项目。其余设定保持原样。</p>
      )}
      {stale && !rejected && (
        <p className="notice">这份草案依据的构思或故事修订已经变化，请按当前版本重新生成。</p>
      )}
      {!!issues.length && (
        <details>
          <summary>草案核对提示 · {issues.length} 项</summary>
          <ul className="conception-issues">
            {issues.map((issue, index) => (
              <li key={`${issue.category_id}-${index}`}>
                {categories.find((category) => category.id === issue.category_id)?.title || '草案'} ·{' '}
                {issue.message}
              </li>
            ))}
          </ul>
        </details>
      )}
      {modelReview && (
        <details>
          <summary>AI 一致性复核</summary>
          <p className="small">AI 根据生成时的完整草案提供建议，采用前请结合你的创作方向核对。</p>
          {modelReview.issues.length ? (
            <ul className="conception-issues">
              {modelReview.issues.map((issue, index) => (
                <li key={`${issue.category_id}-${index}`}>
                  {categories.find((category) => category.id === issue.category_id)?.title || '草案'} ·{' '}
                  {issue.message}
                </li>
              ))}
            </ul>
          ) : (
            <p className="small muted">AI 本次未提出一致性问题。</p>
          )}
        </details>
      )}
      <fieldset disabled={busy || stale || rejected}>
        <legend>选择草案项目</legend>
        {edits.map((category, index) => (
          <details key={category.id} open={index === 0}>
            <summary>{categories.find((current) => current.id === category.id)?.title || '历史类别'}</summary>
            {category.items.map((item) => (
              <fieldset className="conception-item" key={item.id}>
                <legend>{item.title || '未命名项目'}</legend>
                <label className="check">
                  <input
                    type="checkbox"
                    aria-label={`选择 ${item.title || '未命名项目'}`}
                    checked={chosen.includes(item.id)}
                    disabled={currentIds.has(item.id)}
                    onChange={(event) =>
                      setChosen((ids) =>
                        event.target.checked ? [...ids, item.id] : ids.filter((id) => id !== item.id),
                      )
                    }
                  />
                  {currentIds.has(item.id) ? '已有项目，保留当前设定' : '选择这个项目'}
                </label>
                {evidence?.[item.id] && (
                  <ConceptionQuote
                    source={evidence[item.id]}
                    edited={
                      item.detail !== originalItems.get(item.id)?.detail ||
                      item.title !== originalItems.get(item.id)?.title
                    }
                  />
                )}
                <ConceptionItemEditor
                  item={item}
                  choices={choices}
                  worldSetting={category.id === 'world'}
                  onChange={(next) =>
                    setEdits((rows) =>
                      rows.map((row) =>
                        row.id === category.id
                          ? { ...row, items: row.items.map((entry) => (entry.id === next.id ? next : entry)) }
                          : row,
                      ),
                    )
                  }
                />
              </fieldset>
            ))}
          </details>
        ))}
        {!edits.some((category) => category.items.length) && (
          <p>
            {evidence ? '提取已完成，所选保存稿未找到可确认的世界规则或资源。' : '这份草案还没有可采用项目。'}
          </p>
        )}
        {missingLink && <p className="notice">所选项目有关联项未采用。请一并勾选，或在草案中移除该关联。</p>}
        <button
          className="primary"
          type="button"
          disabled={
            !selected.length ||
            missingLink ||
            selected.some((row) => row.items.some((item) => !item.title.trim()))
          }
          onClick={() => onAdopt(selected)}
        >
          采用所选构思
        </button>
      </fieldset>
      {onReject && (
        <button
          type="button"
          disabled={busy}
          onClick={() => {
            if (window.confirm('拒绝整份草案？原草案保留供历史查看，已经采用的项目继续保留。')) onReject()
          }}
        >
          拒绝整份草案
        </button>
      )}
    </section>
  )
}
