import { useEffect, useRef } from 'react'
import { Compartment, EditorState } from '@codemirror/state'
import { EditorView, keymap, lineNumbers, highlightActiveLine, drawSelection } from '@codemirror/view'
import { history, historyKeymap, defaultKeymap } from '@codemirror/commands'
import { markdown } from '@codemirror/lang-markdown'
import { MergeView } from '@codemirror/merge'

export type Selection = { start: number; end: number; text: string }
export function Editor({
  id,
  initial,
  cursor = 0,
  readOnly = false,
  onChange,
  onSelection,
}: {
  id: string
  initial: string
  cursor?: number
  readOnly?: boolean
  onChange: (text: string) => void
  onSelection: (selection: Selection) => void
}) {
  const mount = useRef<HTMLDivElement>(null)
  const instance = useRef<EditorView | null>(null)
  const permission = useRef(new Compartment())
  const callbacks = useRef({ onChange, onSelection })
  callbacks.current = { onChange, onSelection }
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
            }
          }),
          EditorView.theme({
            '&': { height: '100%', fontSize: '17px' },
            '.cm-scroller': { fontFamily: 'inherit', lineHeight: '1.95', overflow: 'auto' },
            '.cm-content': { padding: '28px 24px 120px' },
            '.cm-gutters': { background: 'transparent', color: '#66716d', border: 'none', fontSize: '12px' },
            '.cm-activeLine': { background: '#edf4f130' },
            '&.cm-focused': { outline: '2px solid #327260', outlineOffset: '-2px' },
          }),
        ],
      }),
    })
    instance.current = view
    return () => {
      instance.current = null
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
