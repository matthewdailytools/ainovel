import type { components } from './api-schema'

type Schemas = components['schemas']
export type StoryPayload =
  Schemas['Character'] | Schemas['Event'] | Schemas['State'] | Schemas['Promise'] | Schemas['Relationship']
export type StoryRow = {
  id: string
  kind: StoryPayload['kind']
  revision: number
  payload: StoryPayload
  archived: number
  invalid_reasons: string[]
}
