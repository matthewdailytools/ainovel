import { useId } from 'react'
import { frozenStructureOutput, structureOutputChoice, type StructureOutputChoice } from './structureOutput'

export function StructureOutputSettings({
  value,
  disabled,
  onChange,
}: {
  value: StructureOutputChoice
  disabled: boolean
  onChange: (value: StructureOutputChoice) => void
}) {
  const noteId = useId()
  return (
    <details>
      <summary>结构整理的输出设置</summary>
      <label>
        首轮结构输出上限（tokens）
        <select
          value={value}
          disabled={disabled}
          aria-describedby={noteId}
          onChange={(event) => onChange(structureOutputChoice(event.target.value))}
        >
          <option value={8192}>8192（默认）</option>
          <option value={16384}>16384</option>
        </select>
      </label>
      <p id={noteId} className="small muted">
        仅控制首轮结构整理。已知达到输出上限时，有限修正最高可使用16384；每次调用仍受模型能力及同一任务、共享预算保护。选择16384可能增加费用预留，不保证结果完整或质量通过。已创建任务沿用冻结设置，改变这里不会升级旧任务。
      </p>
    </details>
  )
}

export function FrozenStructureOutputPolicy({
  value,
  maintenance,
}: {
  value: unknown
  maintenance: boolean
}) {
  const policy = frozenStructureOutput(value)
  return (
    <p className="small muted" aria-label="本批次结构输出政策">
      {!maintenance ? (
        '结构维护未启用，本批次不使用结构输出设置。'
      ) : policy ? (
        <>
          首轮结构输出上限：{policy.tokens} tokens。
          {policy.legacy && '历史任务未单独设置，沿用8192，不因当前表单改变。'}
          已知输出截断的有限修正最高可使用16384，仍受模型与预算限制；恢复沿用本批冻结值。
        </>
      ) : (
        '本批次结构输出设置暂无法核对，请查看保存的任务记录；当前表单不会覆盖该设置。'
      )}
    </p>
  )
}
