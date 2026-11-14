import type { Artifact } from './api'

export function latestArtifact(current: Artifact | undefined, incoming: Artifact): Artifact {
  if (!current || current.id !== incoming.id) return incoming
  if (current.candidate_revision > incoming.candidate_revision) return current
  if (current.status !== 'candidate' && incoming.status === 'candidate') return current
  return incoming
}

export function refreshArtifactList(current: Artifact[], incoming: Artifact[]): Artifact[] {
  const known = new Map(current.map((row) => [row.id, row]))
  return incoming.map((row) => latestArtifact(known.get(row.id), row))
}
