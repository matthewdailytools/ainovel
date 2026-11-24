import { useEffect, useRef } from 'react'
import { Compartment, EditorState } from '@codemirror/state'
import {
  EditorView,
  keymap,
  lineNumbers,
  highlightActiveLine,
  drawSelection,
  placeholder,
} from '@codemirror/view'
import { history, historyKeymap, defaultKeymap } from '@codemirror/commands'
import { markdown } from '@codemirror/lang-markdown'
import { MergeView } from '@codemirror/merge'

export type Selection = { start: number; end: number; text: string }
export function Editor({
  id,
  initial,
  cursor = 0,
  scrollTop,
  revealSelection,
  readOnly = false,
  focusOnOpen = false,
  onChange,
  onSelection,
  onViewPosition,
}: {
  id: string
  initial: string
  cursor?: number
  scrollTop?: number
  revealSelection?: { start: number; end: number }
  readOnly?: boolean
  focusOnOpen?: boolean
  onChange: (text: string) => void
  onSelection: (selection: Selection) => void
  onViewPosition?: (cursor: number, scrollTop: number) => void
}) {
  const mount = useRef<HTMLDivElement>(null)
  const instance = useRef<EditorView | null>(null)
  const focusedId = useRef<string | undefined>(undefined)
  const permission = useRef(new Compartment())
  const callbacks = useRef({ onChange, onSelection, onViewPosition })
  callbacks.current = { onChange, onSelection, onViewPosition }
  useEffect(() => {
    const view = new EditorView({
      parent: mount.current!,
      state: EditorState.create({
        doc: initial,
        selection: { anchor: Math.min(cursor, initial.length) },
        extensions: [
          lineNumbers(),
          permission.current.of([EditorState.readOnly.of(readOnly), EditorView.editable.of(!readOnly)]),
          history(),
          drawSelection(),
          highlightActiveLine(),
          placeholder('在这里开始写正文…'),
          markdown(),
          EditorView.lineWrapping,
          keymap.of([...defaultKeymap, ...historyKeymap]),
          EditorView.contentAttributes.of({ 'aria-label': '章节正文', spellcheck: 'false' }),
          EditorView.updateListener.of((update) => {
            if (update.docChanged) callbacks.current.onChange(update.state.doc.toString())
            if (update.selectionSet || update.docChanged) {
              const range = update.state.selection.main
              callbacks.current.onSelection({
                start: range.from,
                end: range.to,
                text: update.state.sliceDoc(range.from, range.to),
              })
              callbacks.current.onViewPosition?.(range.head, update.view.scrollDOM.scrollTop)
            }
          }),
          EditorView.theme({
            '&': { height: '100%' },
            '.cm-scroller': {
              fontFamily: 'inherit',
              lineHeight: 'var(--manuscript-line-height)',
              overflow: 'auto',
            },
            '.cm-content': { padding: '16px var(--editor-inline-padding) 120px', lineBreak: 'strict' },
            '.cm-gutters': { background: 'transparent', color: '#66716d', border: 'none', fontSize: '12px' },
            '.cm-activeLine': { background: '#edf4f130' },
          }),
        ],
      }),
    })
    const savePosition = () =>
      callbacks.current.onViewPosition?.(view.state.selection.main.head, view.scrollDOM.scrollTop)
    view.scrollDOM.addEventListener('scroll', savePosition)
    if (cursor && scrollTop === undefined) {
      view.dispatch({ effects: EditorView.scrollIntoView(Math.min(cursor, initial.length)) })
    } else if (scrollTop !== undefined) {
      view.requestMeasure({
        read: () => scrollTop,
        write: (position) => {
          view.scrollDOM.scrollTop = position
        },
      })
    }
    instance.current = view
    return () => {
      instance.current = null
      view.scrollDOM.removeEventListener('scroll', savePosition)
      view.destroy()
    }
    // Recreate only when a confirmed document identity/version changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])
  useEffect(() => {
    instance.current?.dispatch({
      effects: permission.current.reconfigure([
        EditorState.readOnly.of(readOnly),
        EditorView.editable.of(!readOnly),
      ]),
    })
  }, [readOnly, id])
  useEffect(() => {
    const view = instance.current
    if (!view || !focusOnOpen || readOnly || focusedId.current === id) return
    focusedId.current = id
    mount.current?.scrollIntoView({ block: 'nearest' })
    view.focus()
  }, [focusOnOpen, id, readOnly])
  useEffect(() => {
    const view = instance.current
    if (!view || !revealSelection || revealSelection.end > view.state.doc.length) return
    const { start, end } = revealSelection
    view.dispatch({
      selection: { anchor: start, head: end },
      effects: EditorView.scrollIntoView(start, { y: 'center' }),
    })
    view.focus()
  }, [id, revealSelection])
  return <div className="editor" ref={mount} />
}

export function Difference({
  before,
  after,
  beforeLabel = '当前正文',
}: {
  before: string
  after: string
  beforeLabel?: string
}) {
  const mount = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const view = new MergeView({
      parent: mount.current!,
      a: {
        doc: before,
        extensions: [
          EditorState.readOnly.of(true),
          EditorView.editable.of(false),
          EditorView.lineWrapping,
          EditorView.contentAttributes.of({ 'aria-label': beforeLabel }),
        ],
      },
      b: {
        doc: after,
        extensions: [
          EditorState.readOnly.of(true),
          EditorView.editable.of(false),
          EditorView.lineWrapping,
          EditorView.contentAttributes.of({ 'aria-label': '建议正文' }),
        ],
      },
      highlightChanges: true,
      gutter: true,
    })
    return () => view.destroy()
  }, [before, after, beforeLabel])
  return <div className="difference" ref={mount} />
}
