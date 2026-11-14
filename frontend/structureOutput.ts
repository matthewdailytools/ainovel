export const DEFAULT_STRUCTURE_OUTPUT_TOKENS = 8192
export type StructureOutputChoice = 8192 | 16384

export function structureOutputChoice(value: string): StructureOutputChoice {
  if (value === '8192') return 8192
  if (value === '16384') return 16384
  throw new Error('请选择 8192 或 16384 的首轮结构输出上限。')
}

export function workflowStructureOutput(value: StructureOutputChoice, maintenance: boolean) {
  return maintenance ? value : DEFAULT_STRUCTURE_OUTPUT_TOKENS
}

export function frozenStructureOutput(value: unknown) {
  if (value === undefined) return { tokens: DEFAULT_STRUCTURE_OUTPUT_TOKENS, legacy: true }
  if (typeof value === 'number' && Number.isInteger(value) && value >= 64 && value <= 16384)
    return { tokens: value, legacy: false }
  return undefined
}
