export interface paths {
  '/api/session': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Session */
    get: operations['session_api_session_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/config': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Configuration */
    get: operations['configuration_api_config_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/novels': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Novels */
    get: operations['novels_api_novels_get']
    put?: never
    /** Create Novel */
    post: operations['create_novel_api_novels_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/novels/{novel_id}': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    /** Rename Novel */
    patch: operations['rename_novel_api_novels__novel_id__patch']
    trace?: never
  }
  '/api/novels/{novel_id}/branches': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Branches */
    get: operations['branches_api_novels__novel_id__branches_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/chapters': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Chapters */
    get: operations['chapters_api_branches__branch_id__chapters_get']
    put?: never
    /** Create Chapter */
    post: operations['create_chapter_api_branches__branch_id__chapters_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/chapters/{chapter_id}': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Chapter */
    get: operations['chapter_api_chapters__chapter_id__get']
    /** Save */
    put: operations['save_api_chapters__chapter_id__put']
    post?: never
    delete?: never
    options?: never
    head?: never
    /** Rename Chapter */
    patch: operations['rename_chapter_api_chapters__chapter_id__patch']
    trace?: never
  }
  '/api/chapters/{chapter_id}/history': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** History */
    get: operations['history_api_chapters__chapter_id__history_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/archived': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Archived Chapters */
    get: operations['archived_chapters_api_branches__branch_id__archived_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/chapters/{chapter_id}/archive': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Archive Chapter */
    post: operations['archive_chapter_api_chapters__chapter_id__archive_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/chapters/{chapter_id}/move': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Move Chapter */
    post: operations['move_chapter_api_chapters__chapter_id__move_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/chapters/{chapter_id}/restore': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Restore */
    post: operations['restore_api_chapters__chapter_id__restore_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/reading-state': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Reading State */
    get: operations['reading_state_api_branches__branch_id__reading_state_get']
    /** Save Reading State */
    put: operations['save_reading_state_api_branches__branch_id__reading_state_put']
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/modules': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Module Specs */
    get: operations['module_specs_api_modules_get']
    put?: never
    /** Run Module */
    post: operations['run_module_api_modules_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/workflows': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Start Workflow */
    post: operations['start_workflow_api_workflows_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/definitions': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Save Definition */
    post: operations['save_definition_api_branches__branch_id__definitions_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/tasks': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Tasks */
    get: operations['tasks_api_tasks_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/tasks/{task_id}': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Task */
    get: operations['task_api_tasks__task_id__get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/tasks/{task_id}/transcript': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Transcript */
    get: operations['transcript_api_tasks__task_id__transcript_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/tasks/{task_id}/events': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Events */
    get: operations['events_api_tasks__task_id__events_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/tasks/{task_id}/cancel': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Cancel */
    post: operations['cancel_api_tasks__task_id__cancel_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/tasks/{task_id}/pause': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Pause */
    post: operations['pause_api_tasks__task_id__pause_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/tasks/{task_id}/resume': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Resume */
    post: operations['resume_api_tasks__task_id__resume_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/artifacts': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Artifacts */
    get: operations['artifacts_api_artifacts_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/artifacts/{artifact_id}': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Artifact */
    get: operations['artifact_api_artifacts__artifact_id__get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    /** Edit Candidate */
    patch: operations['edit_candidate_api_artifacts__artifact_id__patch']
    trace?: never
  }
  '/api/artifacts/{artifact_id}/accept': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Accept Candidate */
    post: operations['accept_candidate_api_artifacts__artifact_id__accept_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/artifacts/{artifact_id}/reject': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Reject */
    post: operations['reject_api_artifacts__artifact_id__reject_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/context': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Context */
    get: operations['context_api_branches__branch_id__context_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/facts': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Facts */
    get: operations['facts_api_branches__branch_id__facts_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/search': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Search Manuscript */
    get: operations['search_manuscript_api_branches__branch_id__search_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/facts': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Add Fact */
    post: operations['add_fact_api_facts_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/facts/{fact_id}': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    post?: never
    /** Invalidate Fact */
    delete: operations['invalidate_fact_api_facts__fact_id__delete']
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/feedback': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Feedback */
    get: operations['feedback_api_branches__branch_id__feedback_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/feedback': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Add Feedback */
    post: operations['add_feedback_api_feedback_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/completion': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Completion */
    get: operations['completion_api_branches__branch_id__completion_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/assessments': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Assess */
    post: operations['assess_api_assessments_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/export': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Export */
    get: operations['export_api_branches__branch_id__export_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/import': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Import Chapter */
    post: operations['import_chapter_api_branches__branch_id__import_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/library/books': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Books */
    get: operations['books_api_library_books_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/library/import': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Import Book */
    post: operations['import_book_api_library_import_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/library/search': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Search */
    get: operations['search_api_library_search_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/library/books/{book_id}/units': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Units */
    get: operations['units_api_library_books__book_id__units_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/library/units/{unit_id}': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Unit */
    get: operations['unit_api_library_units__unit_id__get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/library/excerpts': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Excerpts */
    get: operations['excerpts_api_library_excerpts_get']
    put?: never
    /** Excerpt */
    post: operations['excerpt_api_library_excerpts_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/library/catalog': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Catalog */
    get: operations['catalog_api_library_catalog_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/library/jobs': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Download Jobs */
    get: operations['download_jobs_api_library_jobs_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/library/download': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Download */
    post: operations['download_api_library_download_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/library/jobs/{job_id}/cancel': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Cancel Download */
    post: operations['cancel_download_api_library_jobs__job_id__cancel_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/library/books/{book_id}/progress': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Book Progress */
    get: operations['book_progress_api_library_books__book_id__progress_get']
    /** Save Book Progress */
    put: operations['save_book_progress_api_library_books__book_id__progress_put']
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/backups': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Backups */
    get: operations['backups_api_backups_get']
    put?: never
    /** Backup */
    post: operations['backup_api_backups_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/backups/{backup_id}/verify': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Verify Backup */
    post: operations['verify_backup_api_backups__backup_id__verify_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
}
export type webhooks = Record<string, never>
export interface components {
  schemas: {
    /** AcceptInput */
    AcceptInput: {
      /** Idempotency Key */
      idempotency_key: string
      /**
       * Candidate Revision
       * @default 0
       */
      candidate_revision: number
    }
    /** ArchiveInput */
    ArchiveInput: {
      /** Base Revision */
      base_revision: number
      /** Archived */
      archived: boolean
    }
    /** AssessmentInput */
    AssessmentInput: {
      /** Branch Id */
      branch_id: string
      /**
       * Category
       * @enum {string}
       */
      category: 'engineering' | 'creative' | 'benefit'
      /**
       * Result
       * @enum {string}
       */
      result: 'passed' | 'failed' | 'unverified'
      /** Note */
      note: string
      /** Minutes */
      minutes?: {
        [key: string]: number
      }
    }
    /** Body_import_book_api_library_import_post */
    Body_import_book_api_library_import_post: {
      /** File */
      file: string
      /**
       * Encoding
       * @default utf-8-sig
       */
      encoding: string
    }
    /** Body_import_chapter_api_branches__branch_id__import_post */
    Body_import_chapter_api_branches__branch_id__import_post: {
      /** File */
      file: string
    }
    /** CandidateEdit */
    CandidateEdit: {
      /** Text */
      text: string
      /**
       * Candidate Revision
       * @default 0
       */
      candidate_revision: number
    }
    /** CandidateVersionInput */
    CandidateVersionInput: {
      /**
       * Candidate Revision
       * @default 0
       */
      candidate_revision: number
    }
    /** ChapterInput */
    ChapterInput: {
      /** Title */
      title: string
    }
    /** DefinitionInput */
    DefinitionInput: {
      /**
       * Kind
       * @enum {string}
       */
      kind: 'intent' | 'reader' | 'world' | 'characters' | 'outline'
      /** Text */
      text: string
      /**
       * Audience
       * @default 不限
       * @enum {string}
       */
      audience: '不限' | '男频' | '女频'
      /** Genres */
      genres?: string[]
      /** Mechanisms */
      mechanisms?: string[]
      /** Base Story Revision */
      base_story_revision: number
    }
    /** DownloadInput */
    DownloadInput: {
      /** Title */
      title: string
      /**
       * Format
       * @default epub
       * @enum {string}
       */
      format: 'epub' | 'page_text'
    }
    /** ExcerptInput */
    ExcerptInput: {
      /** Unit Id */
      unit_id: string
      /** Start */
      start: number
      /** End */
      end: number
      /** Quote */
      quote: string
      /**
       * Note
       * @default
       */
      note: string
      /** Novel Id */
      novel_id?: string | null
    }
    /** FactInput */
    FactInput: {
      /** Subject */
      subject: string
      /**
       * Kind
       * @enum {string}
       */
      kind: 'event' | 'knowledge' | 'capability' | 'resource' | 'relationship' | 'promise' | 'world'
      /** Statement */
      statement: string
      /**
       * Truth
       * @default fact
       * @enum {string}
       */
      truth: 'fact' | 'belief' | 'uncertain' | 'plan'
      /**
       * Story Time
       * @default 未知
       */
      story_time: string
      /**
       * Evidence
       * @description 有可引用原文列表时只填source_id编号，由后端回填固定版本原文；否则填连续逐字原文。
       */
      evidence: string
      /** Chapter Id */
      chapter_id: string
      /** Source Revision */
      source_revision: number
    }
    /** FeedbackInput */
    FeedbackInput: {
      /** Chapter Id */
      chapter_id: string
      /**
       * Category
       * @enum {string}
       */
      category: '事实错误' | '理解问题' | '审美偏好' | '待判断'
      /** Text */
      text: string
      /**
       * Source Quote
       * @default
       */
      source_quote: string
    }
    /** HTTPValidationError */
    HTTPValidationError: {
      /** Detail */
      detail?: components['schemas']['ValidationError'][]
    }
    /** ModuleInput */
    ModuleInput: {
      /** Client Request Id */
      client_request_id?: string | null
      /**
       * Action
       * @enum {string}
       */
      action:
        | 'writing.advise'
        | 'writing.rewrite_selection'
        | 'writing.expand_plot'
        | 'review.chapter'
        | 'memory.extract'
        | 'outline.plan'
        | 'character.generate'
        | 'world.generate'
        | 'ideation.explore'
      /** Novel Id */
      novel_id: string
      /** Branch Id */
      branch_id: string
      /** Chapter Id */
      chapter_id?: string | null
      /** Base Revision */
      base_revision?: number | null
      /** Instruction */
      instruction: string
      /** Selection Start */
      selection_start?: number | null
      /** Selection End */
      selection_end?: number | null
      /** Selection Text */
      selection_text?: string | null
      /** Fixed */
      fixed?: string[]
      /**
       * Writing Profile
       * @default 第三人称限定视角，现代白话，动作有因果，避免重复解释。
       */
      writing_profile: string
      /** Reference Ids */
      reference_ids?: string[]
      /** Account */
      account: string
      /**
       * Max Output Tokens
       * @default 8192
       */
      max_output_tokens: number
      /** Budget Limit */
      budget_limit?: number | null
    }
    /** MoveInput */
    MoveInput: {
      /**
       * Direction
       * @enum {string}
       */
      direction: 'up' | 'down'
      /** Base Story Revision */
      base_story_revision: number
    }
    /** NovelInput */
    NovelInput: {
      /** Title */
      title: string
      /**
       * Brief
       * @default
       */
      brief: string
    }
    /** Plot */
    Plot: {
      /** Number */
      number: number
      /** Title */
      title: string
      /** Plot */
      plot: string
      /** Fixed */
      fixed?: string[]
    }
    /** ReadingInput */
    ReadingInput: {
      /** Chapter Id */
      chapter_id: string
      /** Cursor */
      cursor: number
      /**
       * Note
       * @default
       */
      note: string
      /** Base Revision */
      base_revision: number
    }
    /** RenameInput */
    RenameInput: {
      /** Title */
      title: string
    }
    /** RestoreInput */
    RestoreInput: {
      /** Base Revision */
      base_revision: number
      /** Revision Id */
      revision_id: string
    }
    /** ResumeInput */
    ResumeInput: {
      /**
       * Acknowledge Unknown
       * @default false
       */
      acknowledge_unknown: boolean
      /**
       * Acknowledge Changes
       * @default false
       */
      acknowledge_changes: boolean
      /**
       * Regenerate Pending
       * @default false
       */
      regenerate_pending: boolean
      /**
       * Reason
       * @default
       */
      reason: string
      /**
       * Candidate Revision
       * @default 0
       */
      candidate_revision: number
    }
    /** SaveInput */
    SaveInput: {
      /** Base Revision */
      base_revision: number
      /** Content */
      content: string
    }
    /** ValidationError */
    ValidationError: {
      /** Location */
      loc: (string | number)[]
      /** Message */
      msg: string
      /** Error Type */
      type: string
      /** Input */
      input?: unknown
      /** Context */
      ctx?: Record<string, never>
    }
    /** WorkflowInput */
    WorkflowInput: {
      /** Client Request Id */
      client_request_id?: string | null
      /** Novel Id */
      novel_id: string
      /** Source Branch Id */
      source_branch_id?: string | null
      /**
       * Mode
       * @enum {string}
       */
      mode: 'assisted' | 'automatic'
      /** Brief */
      brief: string
      /** Fixed */
      fixed?: string[]
      /** Plots */
      plots?: components['schemas']['Plot'][]
      /**
       * Chapters
       * @default 36
       */
      chapters: number
      /**
       * Min Chars
       * @default 2500
       */
      min_chars: number
      /**
       * Max Chars
       * @default 3500
       */
      max_chars: number
      /** Account */
      account: string
      /**
       * Adoption
       * @default manual
       * @enum {string}
       */
      adoption: 'manual' | 'automatic'
      /**
       * Writing Profile
       * @default 第三人称限定视角，克制白话，人物选择有依据，遵守能力和信息边界。
       */
      writing_profile: string
      /** Budget Limit */
      budget_limit?: number | null
    }
  }
  responses: never
  parameters: never
  requestBodies: never
  headers: never
  pathItems: never
}
export type $defs = Record<string, never>
export interface operations {
  session_api_session_get: {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
    }
  }
  configuration_api_config_get: {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
    }
  }
  novels_api_novels_get: {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
    }
  }
  create_novel_api_novels_post: {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['NovelInput']
      }
    }
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  rename_novel_api_novels__novel_id__patch: {
    parameters: {
      query?: never
      header?: never
      path: {
        novel_id: string
      }
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['RenameInput']
      }
    }
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  branches_api_novels__novel_id__branches_get: {
    parameters: {
      query?: never
      header?: never
      path: {
        novel_id: string
      }
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  chapters_api_branches__branch_id__chapters_get: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
      }
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  create_chapter_api_branches__branch_id__chapters_post: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
      }
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['ChapterInput']
      }
    }
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  chapter_api_chapters__chapter_id__get: {
    parameters: {
      query?: never
      header?: never
      path: {
        chapter_id: string
      }
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  save_api_chapters__chapter_id__put: {
    parameters: {
      query?: never
      header?: never
      path: {
        chapter_id: string
      }
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['SaveInput']
      }
    }
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  rename_chapter_api_chapters__chapter_id__patch: {
    parameters: {
      query?: never
      header?: never
      path: {
        chapter_id: string
      }
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['RenameInput']
      }
    }
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  history_api_chapters__chapter_id__history_get: {
    parameters: {
      query?: never
      header?: never
      path: {
        chapter_id: string
      }
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  archived_chapters_api_branches__branch_id__archived_get: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
      }
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  archive_chapter_api_chapters__chapter_id__archive_post: {
    parameters: {
      query?: never
      header?: never
      path: {
        chapter_id: string
      }
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['ArchiveInput']
      }
    }
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  move_chapter_api_chapters__chapter_id__move_post: {
    parameters: {
      query?: never
      header?: never
      path: {
        chapter_id: string
      }
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['MoveInput']
      }
    }
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  restore_api_chapters__chapter_id__restore_post: {
    parameters: {
      query?: never
      header?: never
      path: {
        chapter_id: string
      }
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['RestoreInput']
      }
    }
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  reading_state_api_branches__branch_id__reading_state_get: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
      }
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  save_reading_state_api_branches__branch_id__reading_state_put: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
      }
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['ReadingInput']
      }
    }
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  module_specs_api_modules_get: {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
    }
  }
  run_module_api_modules_post: {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['ModuleInput']
      }
    }
    responses: {
      /** @description Successful Response */
      202: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  start_workflow_api_workflows_post: {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['WorkflowInput']
      }
    }
    responses: {
      /** @description Successful Response */
      202: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  save_definition_api_branches__branch_id__definitions_post: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
      }
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['DefinitionInput']
      }
    }
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  tasks_api_tasks_get: {
    parameters: {
      query?: {
        branch_id?: string | null
      }
      header?: never
      path?: never
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  task_api_tasks__task_id__get: {
    parameters: {
      query?: never
      header?: never
      path: {
        task_id: string
      }
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  transcript_api_tasks__task_id__transcript_get: {
    parameters: {
      query?: never
      header?: never
      path: {
        task_id: string
      }
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  events_api_tasks__task_id__events_get: {
    parameters: {
      query?: {
        after?: number
      }
      header?: never
      path: {
        task_id: string
      }
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  cancel_api_tasks__task_id__cancel_post: {
    parameters: {
      query?: never
      header?: never
      path: {
        task_id: string
      }
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  pause_api_tasks__task_id__pause_post: {
    parameters: {
      query?: never
      header?: never
      path: {
        task_id: string
      }
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  resume_api_tasks__task_id__resume_post: {
    parameters: {
      query?: never
      header?: never
      path: {
        task_id: string
      }
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['ResumeInput']
      }
    }
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  artifacts_api_artifacts_get: {
    parameters: {
      query: {
        branch_id: string
        chapter_id?: string | null
      }
      header?: never
      path?: never
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  artifact_api_artifacts__artifact_id__get: {
    parameters: {
      query?: never
      header?: never
      path: {
        artifact_id: string
      }
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  edit_candidate_api_artifacts__artifact_id__patch: {
    parameters: {
      query?: never
      header?: never
      path: {
        artifact_id: string
      }
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['CandidateEdit']
      }
    }
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  accept_candidate_api_artifacts__artifact_id__accept_post: {
    parameters: {
      query?: never
      header?: never
      path: {
        artifact_id: string
      }
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['AcceptInput']
      }
    }
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  reject_api_artifacts__artifact_id__reject_post: {
    parameters: {
      query?: never
      header?: never
      path: {
        artifact_id: string
      }
      cookie?: never
    }
    requestBody?: {
      content: {
        'application/json': components['schemas']['CandidateVersionInput'] | null
      }
    }
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  context_api_branches__branch_id__context_get: {
    parameters: {
      query?: {
        chapter_id?: string | null
      }
      header?: never
      path: {
        branch_id: string
      }
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  facts_api_branches__branch_id__facts_get: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
      }
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  search_manuscript_api_branches__branch_id__search_get: {
    parameters: {
      query: {
        q: string
        before?: number
      }
      header?: never
      path: {
        branch_id: string
      }
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  add_fact_api_facts_post: {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['FactInput']
      }
    }
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  invalidate_fact_api_facts__fact_id__delete: {
    parameters: {
      query?: never
      header?: never
      path: {
        fact_id: string
      }
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  feedback_api_branches__branch_id__feedback_get: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
      }
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  add_feedback_api_feedback_post: {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['FeedbackInput']
      }
    }
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  completion_api_branches__branch_id__completion_get: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
      }
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  assess_api_assessments_post: {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['AssessmentInput']
      }
    }
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  export_api_branches__branch_id__export_get: {
    parameters: {
      query?: {
        format?: string
      }
      header?: never
      path: {
        branch_id: string
      }
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  import_chapter_api_branches__branch_id__import_post: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
      }
      cookie?: never
    }
    requestBody: {
      content: {
        'multipart/form-data': components['schemas']['Body_import_chapter_api_branches__branch_id__import_post']
      }
    }
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  books_api_library_books_get: {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
    }
  }
  import_book_api_library_import_post: {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    requestBody: {
      content: {
        'multipart/form-data': components['schemas']['Body_import_book_api_library_import_post']
      }
    }
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  search_api_library_search_get: {
    parameters: {
      query: {
        q: string
        book_id?: string | null
        novel_id?: string | null
        offset?: number
      }
      header?: never
      path?: never
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  units_api_library_books__book_id__units_get: {
    parameters: {
      query?: never
      header?: never
      path: {
        book_id: string
      }
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  unit_api_library_units__unit_id__get: {
    parameters: {
      query?: never
      header?: never
      path: {
        unit_id: string
      }
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  excerpts_api_library_excerpts_get: {
    parameters: {
      query?: {
        novel_id?: string | null
      }
      header?: never
      path?: never
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  excerpt_api_library_excerpts_post: {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['ExcerptInput']
      }
    }
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  catalog_api_library_catalog_get: {
    parameters: {
      query: {
        q: string
      }
      header?: never
      path?: never
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  download_jobs_api_library_jobs_get: {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
    }
  }
  download_api_library_download_post: {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['DownloadInput']
      }
    }
    responses: {
      /** @description Successful Response */
      202: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  cancel_download_api_library_jobs__job_id__cancel_post: {
    parameters: {
      query?: never
      header?: never
      path: {
        job_id: string
      }
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  book_progress_api_library_books__book_id__progress_get: {
    parameters: {
      query?: never
      header?: never
      path: {
        book_id: string
      }
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  save_book_progress_api_library_books__book_id__progress_put: {
    parameters: {
      query: {
        unit_id: string
        offset: number
        base_revision: number
      }
      header?: never
      path: {
        book_id: string
      }
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
  backups_api_backups_get: {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
    }
  }
  backup_api_backups_post: {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
    }
  }
  verify_backup_api_backups__backup_id__verify_post: {
    parameters: {
      query?: never
      header?: never
      path: {
        backup_id: string
      }
      cookie?: never
    }
    requestBody?: never
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': unknown
        }
      }
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['HTTPValidationError']
        }
      }
    }
  }
}
