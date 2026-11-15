'use strict'

// Independent review prototype. No production data, API calls or model requests.
;(() => {
  const STORAGE_KEY = 'ainovel.editor-prototype.v1'
  const LOCK_KEY = 'ainovel.editor-prototype.write'
  const $ = (id) => document.getElementById(id)
  const count = (text) => [...text].filter((char) => !/\s/u.test(char)).length
  const clone = (value) => JSON.parse(JSON.stringify(value))
  const sample = [
    '雨落在渡口的木棚上，像有人隔着一层薄板，慢慢拨着算盘。',
    '沈砚收起油纸伞，没有立刻进去。他先看了一眼河面。最后一班渡船还拴在岸边，船舱里只点着一盏灯，灯下坐着的人始终背对着他。',
    '账册藏在衣襟里。来时他翻过三遍，第十七页的名字被人刮去，又用淡墨补了两个字。纸上留着一道很浅的毛边，指腹擦过去，仍能觉出些微的阻力。',
    '“找谁？”棚下的人问。',
    '沈砚停在门槛外，把袖口的水拧干，才道：“找一个记账的人。”',
    '那人抬起头。桌上摆着两只茶碗，一只空着，另一只的水面早已没有热气。他的目光从沈砚的脸移到衣襟，又落回那只空碗上。',
    '“你来晚了。”',
    '沈砚没有问晚了多久。他把手从账册上移开，摸到口袋里那枚旧铜钱，指尖抵住缺了半边的边沿。父亲失踪那晚，留下的也是这样一枚钱。',
    '河面传来一声绳索绷紧的轻响。沈砚回头时，舱里的灯灭了。',
  ].join('\n\n')
  function makeChapter(id, title, text, note = '') {
    return {
      id,
      title,
      text,
      note,
      versions: [{ rev: 1, title, text, cause: '初始演示稿', time: new Date().toISOString() }],
    }
  }
  function initialState() {
    return {
      format: 1,
      generation: 0,
      current: 'chapter-3',
      preferences: { fontSize: '18', lineHeight: '1.95', theme: 'light' },
      chapters: [
        makeChapter(
          'chapter-1',
          '第一章 旧账',
          '沈砚在父亲留下的箱底找到一本旧账。\n\n所有人都说账已结清，只有最后一页还留着一道空格。',
        ),
        makeChapter(
          'chapter-2',
          '第二章 河上来信',
          '信是从河对岸送来的。没有落款，只有一个渡口的名字。\n\n沈砚把信折好，压在旧账册的第十七页。',
        ),
        makeChapter(
          'chapter-3',
          '第三章 雨夜渡口',
          sample,
          '下一步：核对船舱里的人。保留“交易失败、不发生战斗”的安排。',
        ),
        makeChapter('chapter-4', '第四章 未寄出的信', ''),
      ],
    }
  }
  function validState(value) {
    const str = (item) => typeof item === 'string'
    if (!value || value.format !== 1 || !Number.isSafeInteger(value.generation) || value.generation < 0)
      return false
    if (!str(value.current) || !Array.isArray(value.chapters) || !value.chapters.length) return false
    const ids = new Set()
    for (const ch of value.chapters) {
      if (!ch || !str(ch.id) || ids.has(ch.id) || !str(ch.title) || !str(ch.text) || !str(ch.note))
        return false
      ids.add(ch.id)
      if (!Array.isArray(ch.versions) || !ch.versions.length) return false
      let lastRev = 0
      for (const version of ch.versions) {
        if (
          !version ||
          !Number.isSafeInteger(version.rev) ||
          version.rev <= lastRev ||
          !str(version.title) ||
          !str(version.text) ||
          !str(version.cause) ||
          !str(version.time)
        )
          return false
        lastRev = version.rev
      }
    }
    const p = value.preferences
    return (
      ids.has(value.current) &&
      p &&
      ['18', '22', '28', '36'].includes(p.fontSize) &&
      ['1.65', '1.95', '2.3'].includes(p.lineHeight) &&
      ['light', 'dark'].includes(p.theme)
    )
  }

  let state = initialState()
  let expectedRaw = null
  let storageBlocked = false
  let dirty = false
  let composing = false
  let editEpoch = 0
  let saveTimer
  let saveChain = Promise.resolve(true)
  let toastTimer
  let taskTimer
  let taskToken = 0
  let activeTask = null
  let candidate = null
  let selection = { start: 0, end: 0, text: '' }
  let replacePlan = null
  const viewPositions = new Map()
  const chapter = () => state.chapters.find((item) => item.id === state.current)
  function error(message) {
    $('persistent-error').textContent = message
    $('persistent-error').hidden = false
  }
  function clearError() {
    if (!storageBlocked) $('persistent-error').hidden = true
  }
  function toast(message) {
    clearTimeout(toastTimer)
    $('toast').textContent = message
    $('toast').hidden = false
    toastTimer = setTimeout(() => {
      $('toast').hidden = true
    }, 4000)
  }
  try {
    expectedRaw = localStorage.getItem(STORAGE_KEY)
    if (expectedRaw !== null) {
      const stored = JSON.parse(expectedRaw)
      if (!validState(stored)) throw new Error('演示存储格式无效')
      state = stored
    }
  } catch {
    storageBlocked = true
    error('无法读取浏览器演示存储或格式不兼容。原存储不会被覆盖；当前显示演示稿，可编辑并导出本章。')
  }
  if (!navigator.locks?.request) {
    storageBlocked = true
    error(
      '当前浏览器不支持此原型的写入锁。内容仅留在本窗口；请用导出本章保留，或通过 localhost 在新版浏览器打开。',
    )
  }

  function renderChapters() {
    $('chapter-list').replaceChildren(
      ...state.chapters.map((ch, index) => {
        const li = document.createElement('li')
        const button = document.createElement('button')
        button.dataset.chapter = ch.id
        button.setAttribute('aria-current', String(ch.id === state.current))
        const number = document.createElement('span')
        number.className = 'chapter-number'
        number.textContent = String(index + 1).padStart(2, '0')
        const name = document.createElement('span')
        name.className = 'chapter-name'
        name.textContent = ch.title || '未命名章节'
        const info = document.createElement('small')
        info.textContent = `${count(ch.text)} 字${ch.id === state.current ? ' · 正在写作' : ch.text ? '' : ' · 待落笔'}`
        name.append(info)
        button.append(number, name)
        li.append(button)
        return li
      }),
    )
    $('book-count').textContent =
      `${state.chapters.length} 章 · 共 ${state.chapters.reduce((sum, ch) => sum + count(ch.text), 0)} 非空白字符`
  }
  function fitEditor() {
    const editor = $('manuscript')
    editor.style.height = 'auto'
    editor.style.height = `${Math.max(420, editor.scrollHeight + 2)}px`
  }
  function updateMeta() {
    const ch = chapter()
    $('chapter-meta').textContent = `${count(ch.text)} 非空白字符 · 标题不计入 · 原创演示小说`
    $('revision-status').textContent = `浏览器修订 ${ch.versions.at(-1).rev}`
    renderChapters()
    updateCandidate()
  }
  function updateSelection() {
    const editor = $('manuscript')
    selection = {
      start: editor.selectionStart,
      end: editor.selectionEnd,
      text: editor.value.slice(editor.selectionStart, editor.selectionEnd),
    }
    $('selection-count').textContent = selection.text
      ? `已选 ${count(selection.text)} 非空白字符`
      : '未选择正文'
    $('target-label').textContent =
      $('action').value === 'continue' && selection.text
        ? '选区末尾 · 续写插入点'
        : selection.text
          ? `选区 · ${count(selection.text)} 非空白字符`
          : '当前光标'
    $('target-preview').textContent = selection.text
      ? selection.text.slice(0, 90) + (selection.text.length > 90 ? '…' : '')
      : '选择一段正文，或在光标处续写。'
    updateScope()
  }
  function updateScope() {
    const items = [
      $('action').value === 'continue'
        ? '光标处插入；演示上下文为当前章'
        : `当前选区（${count(selection.text)} 非空白字符）及当前章`,
      '作者要求；演示固定约定：交易失败、不发生战斗',
    ]
    if ($('include-character').checked) items.push('已选资料：沈砚人物卡（演示）')
    if ($('include-outline').checked) items.push('已选资料：本章计划（演示）')
    $('send-scope').replaceChildren(
      ...items.map((text) => {
        const li = document.createElement('li')
        li.textContent = text
        return li
      }),
    )
  }
  function applyPreferences() {
    const p = state.preferences
    document.documentElement.style.setProperty('--font-size', `${p.fontSize}px`)
    document.documentElement.style.setProperty('--line-height', p.lineHeight)
    document.body.classList.toggle('dark', p.theme === 'dark')
    $('font-size').value = p.fontSize
    $('line-height').value = p.lineHeight
    $('theme').value = p.theme
    fitEditor()
  }
  function renderChapter() {
    const ch = chapter()
    $('chapter-title').value = ch.title
    $('manuscript').value = ch.text
    $('chapter-note').value = ch.note
    $('chapter-kicker').textContent =
      `第一卷 · 沉舟 / ${String(state.chapters.indexOf(ch) + 1).padStart(2, '0')}`
    const position = viewPositions.get(ch.id) || { start: 0, end: 0, scroll: 0 }
    $('manuscript').setSelectionRange(position.start, position.end)
    updateMeta()
    fitEditor()
    updateSelection()
    window.scrollTo(0, position.scroll)
  }
  function markDirty({ content = false } = {}) {
    dirty = true
    if (content) editEpoch++
    $('save-status').textContent = storageBlocked ? '未保存 · 请导出保留' : '未保存'
    updateMeta()
    clearTimeout(saveTimer)
    if (!composing) saveTimer = setTimeout(() => void save('自动保存'), 650)
  }
  function save(cause = '手动保存', forceCurrentRevision = false) {
    clearTimeout(saveTimer)
    saveChain = saveChain.then(async () => {
      if (storageBlocked) {
        $('save-status').textContent = '未保存 · 请导出保留'
        return false
      }
      if ($('fail-save').checked) {
        $('save-status').textContent = '保存失败 · 可重试'
        error('演示：保存失败。最新内容仍在本窗口；可导出本章，或取消“模拟保存失败”后再次保存。')
        return false
      }
      $('save-status').textContent = '保存中…'
      try {
        return await navigator.locks.request(LOCK_KEY, () => {
          // One origin-wide lock plus exact prior snapshot prevents cross-tab last-write wins.
          if (localStorage.getItem(STORAGE_KEY) !== expectedRaw) {
            storageBlocked = true
            error('另一窗口已更新演示稿。本窗口内容仍保留，保存已停止；请先导出本章，再刷新核对。')
            $('save-status').textContent = '版本冲突 · 请导出当前稿'
            return false
          }
          if (!dirty && expectedRaw !== null && !forceCurrentRevision) {
            clearError()
            $('save-status').textContent = '已保存到此浏览器'
            return true
          }
          const snapshot = clone(state)
          for (const ch of snapshot.chapters) {
            const last = ch.versions.at(-1)
            if (
              (forceCurrentRevision && ch.id === snapshot.current) ||
              last.text !== ch.text ||
              last.title !== ch.title
            ) {
              ch.versions.push({
                rev: last.rev + 1,
                title: ch.title,
                text: ch.text,
                cause,
                time: new Date().toISOString(),
              })
            }
          }
          snapshot.generation++
          const raw = JSON.stringify(snapshot)
          localStorage.setItem(STORAGE_KEY, raw)
          expectedRaw = raw
          state = snapshot
          dirty = false
          clearError()
          $('save-status').textContent = '已保存到此浏览器'
          updateMeta()
          return true
        })
      } catch {
        $('save-status').textContent = '保存失败 · 可重试'
        error('浏览器存储写入失败，可能空间不足或权限受限。最新稿仍在本窗口，请导出本章后重试。')
        return false
      }
    })
    return saveChain
  }

  function showDialog(id) {
    const dialog = $(id)
    const previous = document.activeElement
    dialog.addEventListener(
      'close',
      () => {
        if (id === 'find-dialog') $('manuscript').focus({ preventScroll: true })
        else if (previous instanceof HTMLElement && previous.isConnected)
          previous.focus({ preventScroll: true })
      },
      { once: true },
    )
    dialog.showModal()
  }
  for (const button of document.querySelectorAll('[data-close]')) {
    button.addEventListener('click', () => $(button.dataset.close).close())
  }
  function showPanel(name) {
    for (const button of document.querySelectorAll('[data-panel]')) {
      button.setAttribute('aria-pressed', String(button.dataset.panel === name))
      $(`panel-${button.dataset.panel}`).hidden = button.dataset.panel !== name
    }
  }
  for (const button of document.querySelectorAll('[data-panel]'))
    button.addEventListener('click', () => showPanel(button.dataset.panel))

  function toggleInspector(force) {
    const workspace = $('workspace')
    if (matchMedia('(max-width: 1100px)').matches) {
      const open = force ?? !workspace.classList.contains('mobile-inspector')
      workspace.classList.toggle('mobile-inspector', open)
      workspace.classList.remove('mobile-nav')
      workspace.classList.remove('hide-inspector')
      if (!open) $('inspector-toggle').focus()
    } else if (force === undefined) workspace.classList.toggle('hide-inspector')
    else workspace.classList.toggle('hide-inspector', !force)
    workspace.classList.remove('focused')
    $('focus-toggle').textContent = '专注写作'
    $('focus-toggle').setAttribute('aria-pressed', 'false')
    syncToggles()
    fitEditor()
  }
  function syncToggles() {
    const workspace = $('workspace')
    $('nav-toggle').setAttribute(
      'aria-expanded',
      String(getComputedStyle(document.querySelector('.navigator')).display !== 'none'),
    )
    $('inspector-toggle').setAttribute(
      'aria-expanded',
      String(getComputedStyle($('inspector')).display !== 'none'),
    )
    if (workspace.classList.contains('focused')) $('focus-toggle').textContent = '退出专注'
  }
  $('nav-toggle').addEventListener('click', () => {
    const workspace = $('workspace')
    if (matchMedia('(max-width: 760px)').matches) {
      workspace.classList.toggle('mobile-nav')
      workspace.classList.remove('mobile-inspector')
    } else workspace.classList.toggle('hide-nav')
    syncToggles()
    fitEditor()
  })
  $('back-from-nav').addEventListener('click', () => {
    $('workspace').classList.remove('mobile-nav')
    syncToggles()
    $('nav-toggle').focus()
  })
  $('inspector-toggle').addEventListener('click', () => toggleInspector())
  $('inspector-close').addEventListener('click', () => toggleInspector(false))
  $('back-from-inspector').addEventListener('click', () => toggleInspector(false))
  $('focus-toggle').addEventListener('click', () => {
    const focused = $('workspace').classList.toggle('focused')
    $('workspace').classList.remove('mobile-nav', 'mobile-inspector')
    $('focus-toggle').setAttribute('aria-pressed', String(focused))
    $('focus-toggle').textContent = focused ? '退出专注' : '专注写作'
    syncToggles()
    fitEditor()
  })
  window.addEventListener('resize', () => {
    syncToggles()
    fitEditor()
  })

  $('manuscript').addEventListener('input', () => {
    chapter().text = $('manuscript').value
    markDirty({ content: true })
    fitEditor()
    updateSelection()
  })
  $('chapter-title').addEventListener('input', () => {
    chapter().title = $('chapter-title').value
    markDirty({ content: true })
  })
  $('chapter-title').addEventListener('blur', () => {
    if (!$('chapter-title').value.trim()) {
      chapter().title = '未命名章节'
      $('chapter-title').value = chapter().title
      markDirty({ content: true })
    }
  })
  $('chapter-note').addEventListener('input', () => {
    chapter().note = $('chapter-note').value
    markDirty()
  })
  for (const type of ['select', 'mouseup', 'keyup']) $('manuscript').addEventListener(type, updateSelection)
  document.addEventListener('compositionstart', () => {
    composing = true
    clearTimeout(saveTimer)
  })
  document.addEventListener('compositionend', () => {
    composing = false
    if (dirty) saveTimer = setTimeout(() => void save('自动保存'), 650)
  })
  $('save').addEventListener('click', () => void save())
  $('chapter-list').addEventListener('click', async (event) => {
    const button = event.target.closest('[data-chapter]')
    if (!button || button.dataset.chapter === state.current) return
    if (!(await save())) {
      toast('请先保留当前稿并恢复保存，再切换章节。')
      return
    }
    viewPositions.set(state.current, {
      start: $('manuscript').selectionStart,
      end: $('manuscript').selectionEnd,
      scroll: window.scrollY,
    })
    cancelTask('已切换章节，原任务停止。')
    candidate = null
    state.current = button.dataset.chapter
    editEpoch++
    renderChapter()
    $('workspace').classList.remove('mobile-nav')
    syncToggles()
    dirty = true
    await save('切换章节')
    $('manuscript').focus({ preventScroll: true })
  })
  $('new-chapter').addEventListener('click', () => {
    $('new-title').value = ''
    $('new-error').textContent = ''
    showDialog('new-dialog')
  })
  $('new-form').addEventListener('submit', async (event) => {
    event.preventDefault()
    const title = $('new-title').value.trim()
    if (!title) {
      $('new-error').textContent = '请输入章节标题。'
      return
    }
    if (!(await save())) {
      $('new-error').textContent = '当前稿尚未保存，请先导出或恢复保存后重试。'
      return
    }
    cancelTask('新章节已打开，原任务停止。')
    candidate = null
    const ch = makeChapter(`chapter-${crypto.randomUUID()}`, title, '')
    state.chapters.push(ch)
    state.current = ch.id
    editEpoch++
    renderChapter()
    $('new-dialog').close()
    $('workspace').classList.remove('mobile-nav')
    syncToggles()
    dirty = true
    await save('新建章节')
    $('manuscript').focus()
  })

  $('settings-open').addEventListener('click', () => showDialog('settings-dialog'))
  for (const [id, key] of [
    ['font-size', 'fontSize'],
    ['line-height', 'lineHeight'],
    ['theme', 'theme'],
  ]) {
    $(id).addEventListener('change', () => {
      state.preferences[key] = $(id).value
      applyPreferences()
      markDirty()
    })
  }
  $('fail-save').addEventListener('change', () => {
    if (!$('fail-save').checked) toast('已关闭保存失败演示，可点击保存重试。')
  })

  function openFind() {
    $('replace-confirmation').hidden = true
    if (selection.text && selection.text.length < 100) $('find-text').value = selection.text
    findMatches(0)
    showDialog('find-dialog')
  }
  let matchIndex = 0
  let matches = []
  function findMatches(direction = 0) {
    const needle = $('find-text').value
    const ch = chapter()
    matches = []
    if (needle) {
      let start = 0
      while ((start = ch.text.indexOf(needle, start)) !== -1) {
        matches.push(start)
        start += needle.length
      }
    }
    matchIndex = matches.length
      ? ((direction === 0 ? 0 : matchIndex + direction) + matches.length) % matches.length
      : 0
    $('find-status').textContent = !needle
      ? '输入要查找的内容。'
      : matches.length
        ? `第 ${matchIndex + 1} / ${matches.length} 处匹配`
        : '本章没有匹配内容。'
    for (const id of ['find-prev', 'find-next', 'replace-one', 'replace-all'])
      $(id).disabled = !matches.length
    if (matches.length) {
      $('manuscript').setSelectionRange(matches[matchIndex], matches[matchIndex] + needle.length)
      updateSelection()
    }
  }
  $('find-open').addEventListener('click', openFind)
  $('find-text').addEventListener('input', () => {
    $('replace-confirmation').hidden = true
    findMatches()
  })
  $('find-prev').addEventListener('click', () => findMatches(-1))
  $('find-next').addEventListener('click', () => findMatches(1))
  async function applyReplacement(all, plan) {
    const ch = chapter()
    const before = ch.text
    const needle = plan?.needle ?? $('find-text').value
    const replacement = plan?.replacement ?? $('replace-text').value
    if (!needle || !matches.length || (plan && plan.text !== before)) {
      toast('查找基线已变化，请重新查找。')
      return
    }
    if (!(await save('替换前保存'))) return
    if (ch.id !== state.current || chapter().text !== before) {
      toast('正文已变化，请重新查找。')
      return
    }
    const start = matches[matchIndex]
    chapter().text = all
      ? before.split(needle).join(replacement)
      : before.slice(0, start) + replacement + before.slice(start + needle.length)
    $('manuscript').value = chapter().text
    markDirty({ content: true })
    fitEditor()
    findMatches()
    $('replace-confirmation').hidden = true
    await save(all ? '本章全部替换' : '替换一处')
  }
  $('replace-one').addEventListener('click', () => void applyReplacement(false))
  $('replace-all').addEventListener('click', () => {
    replacePlan = { text: chapter().text, needle: $('find-text').value, replacement: $('replace-text').value }
    $('replace-confirmation-text').textContent =
      `将替换「${chapter().title}」中 ${matches.length} 处匹配。替换前版本会保留。`
    $('replace-confirmation').hidden = false
  })
  $('confirm-replace').addEventListener('click', () => void applyReplacement(true, replacePlan))
  $('cancel-replace').addEventListener('click', () => {
    $('replace-confirmation').hidden = true
  })

  $('sample-selection').addEventListener('click', () => {
    const editor = $('manuscript')
    const firstBreak = editor.value.indexOf('\n\n')
    const start = firstBreak < 0 ? 0 : firstBreak + 2
    let end = editor.value.indexOf('\n\n', start)
    if (end < 0) end = editor.value.length
    editor.setSelectionRange(start, end)
    updateSelection()
    toast('示例选区已载入；也可在正文自由选择。')
  })
  $('action').addEventListener('change', updateSelection)
  $('include-character').addEventListener('change', updateScope)
  $('include-outline').addEventListener('change', updateScope)
  function cancelTask(message) {
    taskToken++
    clearTimeout(taskTimer)
    if (activeTask) $('generation-status').textContent = message
    activeTask = null
    $('generate').disabled = false
    $('cancel-generation').hidden = true
  }
  function candidateValid(value) {
    return (
      value &&
      value.chapterId === state.current &&
      value.epoch === editEpoch &&
      value.baseText === chapter().text &&
      value.baseTitle === chapter().title
    )
  }
  function updateCandidate() {
    $('candidate').hidden = !candidate
    if (!candidate) return
    const valid = candidateValid(candidate)
    $('candidate-basis').textContent = `基于「${candidate.baseTitle}」浏览器修订 ${candidate.rev}`
    $('candidate-request').textContent = `${candidate.scope}\n要求：${candidate.instruction}`
    $('candidate-before').textContent = candidate.before || '（光标插入点，原文不替换）'
    $('candidate-after').textContent = candidate.after
    $('candidate-warning').hidden = valid
    $('candidate-warning').textContent = '正文或目标已变化，此候选已过期。请重新生成，原稿保持。'
    $('accept').disabled = !valid
    $('accept').textContent = candidate.action === 'continue' ? '在光标处插入候选' : '采用并替换选区'
  }
  $('generate').addEventListener('click', async () => {
    if (activeTask) return
    updateSelection()
    const action = $('action').value
    if (action !== 'continue' && !selection.text) {
      $('generation-status').textContent = '请先选择一段正文，或载入示例选区。'
      return
    }
    if (!$('instruction').value.trim()) {
      $('generation-status').textContent = '请写下本次调整要求。'
      return
    }
    const frozen = {
      chapterId: state.current,
      baseTitle: chapter().title,
      baseText: chapter().text,
      epoch: editEpoch,
      ...selection,
      ...(action === 'continue' ? { start: selection.end, end: selection.end, text: '' } : {}),
      action,
      instruction: $('instruction').value,
      scope: $('send-scope').textContent,
    }
    $('generate').disabled = true
    if (!(await save('生成前保存'))) {
      $('generate').disabled = false
      $('generation-status').textContent = '请先恢复保存，再生成候选。'
      return
    }
    if (!candidateValid(frozen)) {
      $('generate').disabled = false
      $('generation-status').textContent = '保存期间正文已变化，请重新选择范围。'
      return
    }
    frozen.rev = chapter().versions.at(-1).rev
    const token = ++taskToken
    activeTask = frozen
    candidate = null
    updateCandidate()
    $('cancel-generation').hidden = false
    $('generation-status').textContent = '正在准备本地演示候选…正文可继续编辑。'
    const shouldFail = $('fail-generation').checked
    $('fail-generation').checked = false
    taskTimer = setTimeout(() => {
      if (token !== taskToken || activeTask !== frozen || state.current !== frozen.chapterId) return
      activeTask = null
      $('generate').disabled = false
      $('cancel-generation').hidden = true
      if (shouldFail) {
        $('generation-status').textContent = '演示：生成失败。要求与原稿已保留，可以重试。'
        return
      }
      const after =
        action === 'continue'
          ? '\n\n沈砚没有追船。他蹲下身，在门槛内侧找到一块被雨水浸湿的纸角。上面只剩半个账房的印记。'
          : action === 'expand'
            ? `${frozen.text}\n\n他拇指抵住伞柄，等棚里的脚步声停了，才把伞靠在门外。`
            : '沈砚把伞收拢，停在棚外。河边还拴着最后一班渡船，舱里亮着一盏灯。灯下那人背朝岸边，听见脚步，也没有回头。'
      candidate = { ...frozen, before: action === 'continue' ? '' : frozen.text, after }
      $('generation-status').textContent = '演示候选已就绪，尚未改动正文。'
      updateCandidate()
    }, 900)
  })
  $('cancel-generation').addEventListener('click', () => cancelTask('已取消生成，原稿未改动。'))
  $('reject').addEventListener('click', () => {
    candidate = null
    updateCandidate()
    $('generation-status').textContent = '已放弃候选，正文未改动。'
  })
  $('accept').addEventListener('click', async () => {
    const selected = candidate
    if (!candidateValid(selected)) return
    if (!(await save('采用前保存')) || !candidateValid(selected)) {
      updateCandidate()
      return
    }
    const ch = chapter()
    const end = selected.action === 'continue' ? selected.start : selected.end
    ch.text = ch.text.slice(0, selected.start) + selected.after + ch.text.slice(end)
    $('manuscript').value = ch.text
    $('manuscript').setSelectionRange(selected.start, selected.start + selected.after.length)
    candidate = null
    markDirty({ content: true })
    fitEditor()
    updateSelection()
    const saved = await save('采用演示候选')
    $('generation-status').textContent = saved
      ? '已采用并保存到此浏览器；采用前稿仍在历史中。'
      : '候选已载入正文但保存失败，请导出或重试保存。'
    if (matchMedia('(max-width: 1100px)').matches) toggleInspector(false)
    $('manuscript').focus({ preventScroll: true })
  })

  function showHistoryVersion() {
    const version = chapter().versions.find((v) => String(v.rev) === $('history-version').value)
    $('history-current').textContent = `${chapter().title}\n\n${chapter().text || '（空稿）'}`
    $('history-before').textContent = version
      ? `${version.title}\n\n${version.text || '（空稿）'}`
      : '没有可比较的版本。'
    $('restore').disabled = !version
  }
  $('history-open').addEventListener('click', () => {
    $('history-version').replaceChildren(
      ...[...chapter().versions].reverse().map((version) => {
        const option = document.createElement('option')
        option.value = String(version.rev)
        option.textContent = `修订 ${version.rev} · ${version.cause} · ${new Date(version.time).toLocaleTimeString('zh-CN')}`
        return option
      }),
    )
    showHistoryVersion()
    showDialog('history-dialog')
  })
  $('history-version').addEventListener('change', showHistoryVersion)
  $('restore').addEventListener('click', async () => {
    const target = chapter().versions.find((v) => String(v.rev) === $('history-version').value)
    if (!target || !(await save('历史恢复前保存'))) return
    chapter().text = target.text
    chapter().title = target.title
    candidate = null
    cancelTask('已恢复历史稿，原任务停止。')
    editEpoch++
    dirty = true
    renderChapter()
    const saved = await save(`恢复历史修订 ${target.rev}`, true)
    $('history-dialog').close()
    toast(saved ? '已恢复为新修订，恢复前稿仍保留。' : '已载入历史稿，尚未保存；请导出或重试。')
    $('manuscript').focus({ preventScroll: true })
  })
  $('export').addEventListener('click', () => {
    const ch = chapter()
    const text = `${ch.title}\n\n${ch.text}${ch.note ? `\n\n【原型写作便笺 · 非正文】\n${ch.note}` : ''}\n`
    const url = URL.createObjectURL(new Blob(['\uFEFF', text], { type: 'text/plain;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = `${(ch.title || '未命名章节').replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_')}.txt`
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
    toast('已交给浏览器下载：当前章及单列的写作便笺。')
  })
  window.addEventListener('storage', (event) => {
    if ((event.key === STORAGE_KEY || event.key === null) && event.newValue !== expectedRaw) {
      storageBlocked = true
      $('save-status').textContent = '版本冲突 · 请导出当前稿'
      error('另一窗口已更新或清除了演示存储。本窗口内容仍保留，保存已停止；请先导出本章，再刷新核对。')
    }
  })
  window.addEventListener('beforeunload', (event) => {
    if (dirty) {
      event.preventDefault()
      event.returnValue = ''
    }
  })
  document.addEventListener('keydown', (event) => {
    if (event.isComposing || composing || !(event.ctrlKey || event.metaKey)) return
    if (event.key.toLowerCase() === 's' && !event.shiftKey) {
      event.preventDefault()
      void save()
    }
    if (event.shiftKey && event.key.toLowerCase() === 'f' && !document.querySelector('dialog[open]')) {
      event.preventDefault()
      openFind()
    }
    if (event.shiftKey && event.key === 'Enter' && !document.querySelector('dialog[open]')) {
      event.preventDefault()
      $('focus-toggle').click()
    }
  })

  applyPreferences()
  renderChapter()
  syncToggles()
  new ResizeObserver(() => {
    document.documentElement.style.setProperty(
      '--header-height',
      `${document.querySelector('.app-header').getBoundingClientRect().height}px`,
    )
  }).observe(document.querySelector('.app-header'))
  if (!storageBlocked) void save('初次打开')
  else $('save-status').textContent = '未保存 · 请导出保留'
})()
