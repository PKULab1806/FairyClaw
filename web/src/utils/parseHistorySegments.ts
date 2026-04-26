import type { HistorySegment, PendingFileRef } from '../types/chat'

/** Parse persisted session_event ``segments`` for chat preview and history restore. */
export function parseHistorySegments(raw: unknown): {
  text: string
  images: string[]
  files: PendingFileRef[]
} {
  if (!Array.isArray(raw)) {
    return { text: '', images: [], files: [] }
  }
  const texts: string[] = []
  const images: string[] = []
  const files: PendingFileRef[] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') {
      continue
    }
    const segment = item as HistorySegment
    if (segment.type === 'text') {
      const value =
        typeof segment.text === 'string'
          ? segment.text
          : typeof segment.content === 'string'
            ? segment.content
            : ''
      if (value) {
        texts.push(value)
      }
      continue
    }
    if (segment.type === 'image_url') {
      const url = segment.image_url?.url
      if (typeof url === 'string' && url) {
        images.push(url)
      }
      continue
    }
    if (segment.type === 'file') {
      const fileId = typeof segment.file_id === 'string' ? segment.file_id : ''
      if (fileId) {
        files.push({ fileId, filename: fileId })
      }
    }
  }
  return { text: texts.join('\n\n').trim(), images, files }
}
