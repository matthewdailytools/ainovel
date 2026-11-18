import type { components } from './api-schema'

export type SelectedOutlineCard = components['schemas']['OutlineCardOption']

const maxCards = 8

export function selectedOutlineCardKey(card: SelectedOutlineCard) {
  return card.version_id ?? ''
}

export function selectedOutlineCardReference(card: SelectedOutlineCard) {
  const { session_id, card_id, revision } = card
  return { session_id, card_id, revision, version_id: selectedOutlineCardKey(card) }
}

export function SelectedOutlineCards({
  cards,
  selected,
  onChange,
  onRefresh,
  disabled,
}: {
  cards: SelectedOutlineCard[]
  selected: string[]
  onChange: (versions: string[]) => void
  onRefresh?: () => void
  disabled?: boolean
}) {
  const options = new Map(
    cards.filter(selectedOutlineCardKey).map((card) => [selectedOutlineCardKey(card), card]),
  )
  const stale = selected.some((id) => !options.has(id))
  const selectedCards = selected.map((id) => options.get(id))

  return (
    <>
      {stale && <p role="alert">手法卡的可用修订已变化，请移除失效项并核对所需修订。</p>}
      <details>
        <summary>已保存的手法卡（可选）{selected.length > 0 && ` · 已选 ${selected.length} 张`}</summary>
        <p className="small">另选手法卡时，会向模型发送写法、适用条件、须替换内容和修订标识。</p>
        <fieldset disabled={disabled}>
          <label>
            选用手法卡
            <select
              value=""
              disabled={selected.length >= maxCards || !options.size}
              onChange={(event) => onChange([...selected, event.target.value])}
            >
              <option value="" disabled>
                {options.size ? '选择正式修订' : '暂无可选的正式修订'}
              </option>
              {Array.from(options, ([id, card]) => (
                <option
                  key={id}
                  value={id}
                  disabled={selectedCards.some(
                    (item) => item?.session_id === card.session_id && item.card_id === card.card_id,
                  )}
                >
                  {card.technique} · {card.source_title} · 修订 {card.revision}
                  {card.pinned && ' · 拆解台已钉选'}
                </option>
              ))}
            </select>
          </label>
          {onRefresh && (
            <button type="button" onClick={onRefresh}>
              刷新正式手法卡
            </button>
          )}
          {selected.length >= maxCards && <p className="small">本次最多选用 {maxCards} 张手法卡。</p>}
          {!!selected.length && (
            <ul>
              {selected.map((id) => {
                const card = options.get(id)
                return (
                  <li key={id}>
                    {card ? (
                      <>
                        <p>
                          {card.technique} · {card.source_title} · 修订 {card.revision}
                          {card.pinned && ' · 拆解台已钉选'}
                        </p>
                        <p>适用条件 · {card.conditions}</p>
                        <p>须替换 · {card.exceptions}</p>
                      </>
                    ) : (
                      <p>所选修订已失效</p>
                    )}
                    <button
                      type="button"
                      onClick={() => onChange(selected.filter((item) => item !== id))}
                      aria-label={card ? `移除手法卡 ${card.technique} · 修订 ${card.revision}` : undefined}
                    >
                      {card ? '移除手法卡' : '移除失效手法卡'}
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </fieldset>
      </details>
    </>
  )
}
