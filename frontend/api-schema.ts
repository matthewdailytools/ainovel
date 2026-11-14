export interface paths {
  '/api/branches/{branch_id}/finalization': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Author Finalization */
    get: operations['author_finalization_api_branches__branch_id__finalization_get']
    put?: never
    /** Confirm Author Finalization */
    post: operations['confirm_author_finalization_api_branches__branch_id__finalization_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
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
  '/api/branches/{branch_id}/story-graph': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Get Graph */
    get: operations['get_graph_api_branches__branch_id__story_graph_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/story-records': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Create Record */
    post: operations['create_record_api_branches__branch_id__story_records_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/story-records/{identifier}': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    /** Update Record */
    put: operations['update_record_api_branches__branch_id__story_records__identifier__put']
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/story-records/{identifier}/history': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** History */
    get: operations['history_api_branches__branch_id__story_records__identifier__history_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/story-context': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** At Time */
    get: operations['at_time_api_branches__branch_id__story_context_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/story-inheritance': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Preview */
    get: operations['preview_api_branches__branch_id__story_inheritance_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/chapters/{chapter_id}/scene-context': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Whole Context */
    get: operations['whole_context_api_branches__branch_id__chapters__chapter_id__scene_context_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/chapters/{chapter_id}/scene-times/{identifier}/history': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** History */
    get: operations['history_api_branches__branch_id__chapters__chapter_id__scene_times__identifier__history_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/chapters/{chapter_id}/scene-times': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Get Scenes */
    get: operations['get_scenes_api_branches__branch_id__chapters__chapter_id__scene_times_get']
    put?: never
    /** Create */
    post: operations['create_api_branches__branch_id__chapters__chapter_id__scene_times_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/chapters/{chapter_id}/scene-times/{identifier}': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    /** Update */
    put: operations['update_api_branches__branch_id__chapters__chapter_id__scene_times__identifier__put']
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/import-previews/{identifier}/draft': {
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
    /** Save Draft */
    patch: operations['save_draft_api_branches__branch_id__import_previews__identifier__draft_patch']
    trace?: never
  }
  '/api/branches/{branch_id}/import-previews': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Previews */
    get: operations['previews_api_branches__branch_id__import_previews_get']
    put?: never
    /** Preview */
    post: operations['preview_api_branches__branch_id__import_previews_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/import-previews/{identifier}': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Get Preview */
    get: operations['get_preview_api_branches__branch_id__import_previews__identifier__get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/import-previews/{identifier}/read': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Read Segment */
    post: operations['read_segment_api_branches__branch_id__import_previews__identifier__read_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/import-previews/{identifier}/confirm': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Confirm */
    post: operations['confirm_api_branches__branch_id__import_previews__identifier__confirm_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/genre-catalog': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Genre Catalog */
    get: operations['genre_catalog_api_genre_catalog_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/genre-profile': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Genre Profile */
    get: operations['genre_profile_api_branches__branch_id__genre_profile_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/genre-profile/preview': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Preview Genre Profile */
    post: operations['preview_genre_profile_api_genre_profile_preview_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/author-contract': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Author Contract */
    get: operations['author_contract_api_branches__branch_id__author_contract_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/author-contract/preview': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Preview Author Contract */
    post: operations['preview_author_contract_api_author_contract_preview_post']
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
  '/api/tasks/{task_id}/continue': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Continue With Account */
    post: operations['continue_with_account_api_tasks__task_id__continue_post']
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
  '/api/reference-sessions': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** References */
    get: operations['references_api_reference_sessions_get']
    put?: never
    /** Create Reference */
    post: operations['create_reference_api_reference_sessions_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/reference-sessions/{identifier}': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Get Reference */
    get: operations['get_reference_api_reference_sessions__identifier__get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    /** Edit Reference */
    patch: operations['edit_reference_api_reference_sessions__identifier__patch']
    trace?: never
  }
  '/api/reference-sessions/{identifier}/tasks': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Reference Tasks */
    get: operations['reference_tasks_api_reference_sessions__identifier__tasks_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/reference-sessions/{identifier}/actions': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Start Reference */
    post: operations['start_reference_api_reference_sessions__identifier__actions_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/reference-sessions/{identifier}/feedback': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Feedback */
    post: operations['feedback_api_reference_sessions__identifier__feedback_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/story-extractions': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** History */
    get: operations['history_api_branches__branch_id__story_extractions_get']
    put?: never
    /** Start */
    post: operations['start_api_branches__branch_id__story_extractions_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/story-extractions/{artifact_id}/adopt': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Confirm */
    post: operations['confirm_api_branches__branch_id__story_extractions__artifact_id__adopt_post']
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
    /** AdoptExtraction */
    AdoptExtraction: {
      /** Selected Scene Ids */
      selected_scene_ids?: string[]
      /**
       * Confirm Scene Replacement
       * @default false
       */
      confirm_scene_replacement: boolean
      /** Base Story Revision */
      base_story_revision: number
      /** Candidate Revision */
      candidate_revision: number
      /** Selected Ids */
      selected_ids: string[]
      /** Idempotency Key */
      idempotency_key: string
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
    /** AuthorContractBasis */
    AuthorContractBasis: {
      /** Source Artifact Id */
      source_artifact_id: string | null
      /** Contract Version */
      contract_version: string
    }
    /** AuthorIntentFields */
    AuthorIntentFields: {
      /**
       * Emotion Target
       * @default
       */
      emotion_target: string
      /**
       * Core Hook
       * @default
       */
      core_hook: string
      /**
       * Direction
       * @default
       */
      direction: string
      /** Fixed Results */
      fixed_results?: string[]
      /** Prohibitions */
      prohibitions?: string[]
      /** Open Details */
      open_details?: string[]
      /**
       * Ending
       * @default
       */
      ending: string
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
    /** Body_preview_api_branches__branch_id__import_previews_post */
    Body_preview_api_branches__branch_id__import_previews_post: {
      /** File */
      file: string
      /**
       * Encoding
       * @default utf-8-sig
       */
      encoding: string
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
    /** Character */
    Character: {
      /** Title */
      title: string
      /**
       * Notes
       * @default
       */
      notes: string
      /** Sources */
      sources?: components['schemas']['Source'][]
      /** Depends On */
      depends_on?: string[]
      /**
       * Volume
       * @default
       */
      volume: string
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      kind: 'character'
      /** Aliases */
      aliases?: string[]
      /**
       * Profile
       * @default
       */
      profile: string
    }
    /** Confirm */
    Confirm: {
      /** Draft Revision */
      draft_revision?: number | null
      /** Client Request Id */
      client_request_id: string
      /** Base Story Revision */
      base_story_revision: number
      /** Mappings */
      mappings: components['schemas']['Mapping'][]
    }
    /** ContinuationInput */
    ContinuationInput: {
      /** Client Request Id */
      client_request_id: string
      /** Account */
      account: string
      /** Budget Limit */
      budget_limit?: number | null
      /** Reason */
      reason: string
      /**
       * Acknowledge Unknown
       * @default false
       */
      acknowledge_unknown: boolean
    }
    /** CreateReference */
    CreateReference: {
      /** Unit Id */
      unit_id: string
      /** Start */
      start: number
      /** End */
      end: number
      /** Quote */
      quote: string
      /** Goal */
      goal: string
      /** Branch Id */
      branch_id?: string | null
      /** Chapter Id */
      chapter_id?: string | null
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
      /** Author Exceptions */
      author_exceptions?: string[]
      intent_contract?: components['schemas']['AuthorIntentFields'] | null
      /** Intent Source Candidate Id */
      intent_source_candidate_id?: string | null
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
    /** EditDraft */
    EditDraft: {
      /** Base Revision */
      base_revision: number
      /** Mappings */
      mappings: components['schemas']['Mapping'][]
    }
    /** EditReference */
    EditReference: {
      /** Base Revision */
      base_revision: number
      /** Goal */
      goal: string
      /** Cards */
      cards?: components['schemas']['ReferenceCard'][]
      /** Selected Ids */
      selected_ids?: string[]
      /**
       * Plot
       * @default
       */
      plot: string
      /** Fixed */
      fixed?: string[]
      /**
       * Application Plan
       * @default
       */
      application_plan: string
      /** Branch Id */
      branch_id?: string | null
      /** Chapter Id */
      chapter_id?: string | null
    }
    /** Event */
    Event: {
      /** Title */
      title: string
      /**
       * Notes
       * @default
       */
      notes: string
      /** Sources */
      sources?: components['schemas']['Source'][]
      /** Depends On */
      depends_on?: string[]
      /**
       * Volume
       * @default
       */
      volume: string
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      kind: 'event'
      /**
       * Thread
       * @default 主线
       */
      thread: string
      /** Story Order */
      story_order?: number | null
      /**
       * Story Time
       * @default 未知
       */
      story_time: string
      /**
       * Status
       * @default planned
       * @enum {string}
       */
      status: 'planned' | 'actual' | 'unknown' | 'conflict'
      /** Participants */
      participants?: string[]
      /** After */
      after?: string[]
    }
    /** EventWitness */
    EventWitness: {
      /** Ref */
      ref: string
      /** Event Id */
      event_id: string
      /** Event Revision */
      event_revision?: number | null
      /** Story Order */
      story_order?: number | null
      /** Record Sha256 */
      record_sha256: string
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
    /** FinalizationChecklist */
    FinalizationChecklist: {
      /** Manuscript Read */
      manuscript_read: boolean
      /** Main Conflict Closed */
      main_conflict_closed: boolean
      /** Character Outcomes Reviewed */
      character_outcomes_reviewed: boolean
      /** Promises Addressed */
      promises_addressed: boolean
    }
    /** FinalizationDisposition */
    FinalizationDisposition: {
      /** Issue Id */
      issue_id: string
      /**
       * Decision
       * @enum {string}
       */
      decision: 'not_an_error' | 'intentional_choice'
      /** Reason */
      reason: string
    }
    /** GenreProfileBasis */
    GenreProfileBasis: {
      /** Source Artifact Id */
      source_artifact_id: string | null
      /** Catalog Version */
      catalog_version: string
    }
    /** HTTPValidationError */
    HTTPValidationError: {
      /** Detail */
      detail?: components['schemas']['ValidationError'][]
    }
    /** ManualFinalizationInput */
    ManualFinalizationInput: {
      /** Base Story Revision */
      base_story_revision: number
      /** Base Manifest Sha256 */
      base_manifest_sha256: string
      /** Idempotency Key */
      idempotency_key: string
      checklist: components['schemas']['FinalizationChecklist']
      /** Note */
      note: string
      /** Dispositions */
      dispositions?: components['schemas']['FinalizationDisposition'][]
    }
    /** Mapping */
    Mapping: {
      /** Title */
      title: string
      /** Start */
      start: number
      /** End */
      end: number
      /**
       * Action
       * @default append
       * @enum {string}
       */
      action: 'append' | 'replace' | 'skip'
      /** Chapter Id */
      chapter_id?: string | null
      /** Base Revision */
      base_revision?: number | null
      /**
       * Allow Duplicate
       * @default false
       */
      allow_duplicate: boolean
    }
    /** ModuleInput */
    ModuleInput: {
      /**
       * Plot Contract Check
       * @default false
       */
      plot_contract_check: boolean
      /**
       * Include Scene Ranges
       * @default false
       */
      include_scene_ranges: boolean
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
        | 'story.extract'
        | 'outline.plan'
        | 'character.generate'
        | 'world.generate'
        | 'ideation.explore'
        | 'intent.compile'
        | 'revision.targeted'
        | 'reference.analyze'
        | 'reference.plan'
        | 'reference.draft'
        | 'reference.check'
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
      genre_profile_basis?: components['schemas']['GenreProfileBasis'] | null
      author_contract_basis?: components['schemas']['AuthorContractBasis'] | null
      /** Source Review Id */
      source_review_id?: string | null
      /** Finding Indices */
      finding_indices?: number[]
      /** Reference Session Id */
      reference_session_id?: string | null
      /** Reference Revision */
      reference_revision?: number | null
      /** Story Chapter Ids */
      story_chapter_ids?: string[]
      /**
       * Context Position
       * @default 0
       */
      context_position: number
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
    /** Promise */
    Promise: {
      /** Title */
      title: string
      /**
       * Notes
       * @default
       */
      notes: string
      /** Sources */
      sources?: components['schemas']['Source'][]
      /** Depends On */
      depends_on?: string[]
      /**
       * Volume
       * @default
       */
      volume: string
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      kind: 'promise'
      /**
       * Thread
       * @default 主线
       */
      thread: string
      /** Stages */
      stages: components['schemas']['PromiseStage'][]
    }
    /** PromiseStage */
    PromiseStage: {
      /**
       * Status
       * @enum {string}
       */
      status: 'planned' | 'planted' | 'reinforced' | 'resolved' | 'retained' | 'abandoned'
      /** Event Id */
      event_id?: string | null
      /** Sources */
      sources?: components['schemas']['Source'][]
      /**
       * Note
       * @default
       */
      note: string
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
    /** ReferenceAction */
    ReferenceAction: {
      /**
       * Plot Contract Check
       * @default false
       */
      plot_contract_check: boolean
      /** Base Revision */
      base_revision: number
      /**
       * Action
       * @enum {string}
       */
      action: 'analyze' | 'plan' | 'draft' | 'check'
      /** Account */
      account: string
      /** Client Request Id */
      client_request_id: string
      /** Budget Limit */
      budget_limit?: number | null
      /** Candidate Id */
      candidate_id?: string | null
      /**
       * Check Saved
       * @default false
       */
      check_saved: boolean
    }
    /** ReferenceCard */
    ReferenceCard: {
      /**
       * Id
       * @default
       */
      id: string
      /** Observation */
      observation: string
      /** Interpretation */
      interpretation: string
      /** Technique */
      technique: string
      /** Conditions */
      conditions: string
      /**
       * Exceptions
       * @default
       */
      exceptions: string
      /** Evidence */
      evidence: string
    }
    /** ReferenceFeedback */
    ReferenceFeedback: {
      /** Base Revision */
      base_revision: number
      /** Text */
      text: string
      /** Candidate Id */
      candidate_id?: string | null
    }
    /** Relationship */
    Relationship: {
      /** Title */
      title: string
      /**
       * Notes
       * @default
       */
      notes: string
      /** Sources */
      sources?: components['schemas']['Source'][]
      /** Depends On */
      depends_on?: string[]
      /**
       * Volume
       * @default
       */
      volume: string
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      kind: 'relationship'
      /** From Id */
      from_id: string
      /** To Id */
      to_id: string
      /** Relation */
      relation: string
      /** Event Id */
      event_id?: string | null
      /**
       * Truth
       * @default planned
       * @enum {string}
       */
      truth: 'actual' | 'planned' | 'unknown' | 'conflict'
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
    /** SaveRecord */
    SaveRecord: {
      /** Base Story Revision */
      base_story_revision: number
      /** Base Revision */
      base_revision?: number | null
      /** Record */
      record:
        | components['schemas']['Character']
        | components['schemas']['Event']
        | components['schemas']['State']
        | components['schemas']['Promise']
        | components['schemas']['Relationship']
      /**
       * Archived
       * @default false
       */
      archived: boolean
    }
    /** SaveScene */
    SaveScene: {
      /** Base Story Revision */
      base_story_revision: number
      /** Base Revision */
      base_revision?: number | null
      scene: components['schemas']['SceneTime']
      /**
       * Archived
       * @default false
       */
      archived: boolean
    }
    /** SceneOrderAudit */
    SceneOrderAudit: {
      /**
       * Version
       * @constant
       */
      version: 'scene-event-refs/2'
      /**
       * Kind
       * @enum {string}
       */
      kind: 'event_refs' | 'unknown'
      /**
       * Reason
       * @default
       */
      reason: string
      /** Refs */
      refs?: components['schemas']['EventWitness'][]
      /**
       * Covered Events
       * @description 候选生成时覆盖事件的冻结审计快照，可能未被选择采用；不是当前正式事件依赖。正式依赖仅refs。
       */
      covered_events?: components['schemas']['EventWitness'][]
    }
    /** SceneTime */
    SceneTime: {
      order_basis?: components['schemas']['SceneOrderAudit'] | null
      /** Event Id */
      event_id?: string | null
      /** Event Revision */
      event_revision?: number | null
      /** Title */
      title: string
      /** Source Revision */
      source_revision: number
      /** Start */
      start: number
      /** End */
      end: number
      /** Story Order */
      story_order?: number | null
      /**
       * Story Time
       * @default 未知
       */
      story_time: string
      /**
       * Audience
       * @default character
       * @enum {string}
       */
      audience: 'author' | 'character' | 'reader'
      /** Character Id */
      character_id?: string | null
      /**
       * Note
       * @default
       */
      note: string
    }
    /** Segment */
    Segment: {
      /** Title */
      title: string
      /** Start */
      start: number
      /** End */
      end: number
    }
    /** Source */
    Source: {
      /** Chapter Id */
      chapter_id: string
      /** Revision */
      revision: number
      /** Start */
      start: number
      /** End */
      end: number
      /** Quote */
      quote: string
    }
    /** StartExtraction */
    StartExtraction: {
      /**
       * Max Output Tokens
       * @default 8192
       */
      max_output_tokens: number
      /**
       * Include Scene Ranges
       * @default false
       */
      include_scene_ranges: boolean
      /** Base Story Revision */
      base_story_revision: number
      /** Chapter Ids */
      chapter_ids: string[]
      /** Account */
      account: string
      /** Client Request Id */
      client_request_id: string
      /** Budget Limit */
      budget_limit?: number | null
    }
    /** State */
    State: {
      /** Title */
      title: string
      /**
       * Notes
       * @default
       */
      notes: string
      /** Sources */
      sources?: components['schemas']['Source'][]
      /** Depends On */
      depends_on?: string[]
      /**
       * Volume
       * @default
       */
      volume: string
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      kind: 'state'
      /** Character Id */
      character_id: string
      /** Event Id */
      event_id: string
      /**
       * Category
       * @enum {string}
       */
      category: 'knowledge' | 'capability' | 'resource' | 'injury' | 'other'
      /** Key */
      key: string
      /** Value */
      value: string
      /**
       * Audience
       * @enum {string}
       */
      audience: 'author' | 'character' | 'reader'
      /**
       * Truth
       * @default actual
       * @enum {string}
       */
      truth: 'actual' | 'planned' | 'unknown' | 'conflict'
      /** Until Event Id */
      until_event_id?: string | null
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
      /**
       * Structure Max Output Tokens
       * @default 8192
       */
      structure_max_output_tokens: number
      /**
       * Plot Contract Check
       * @default false
       */
      plot_contract_check: boolean
      /**
       * Completion Scope
       * @default whole_book
       * @enum {string}
       */
      completion_scope: 'whole_book' | 'segment'
      /**
       * Scene Maintenance
       * @default false
       */
      scene_maintenance: boolean
      /**
       * Structured Maintenance
       * @default false
       */
      structured_maintenance: boolean
      /**
       * Character Field Repair
       * @default false
       */
      character_field_repair: boolean
      /** Inherit Story Record Ids */
      inherit_story_record_ids?: string[]
      /** Inherit Source Revision */
      inherit_source_revision?: number | null
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
      genre_profile_basis?: components['schemas']['GenreProfileBasis'] | null
      author_contract_basis?: components['schemas']['AuthorContractBasis'] | null
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
  author_finalization_api_branches__branch_id__finalization_get: {
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
  confirm_author_finalization_api_branches__branch_id__finalization_post: {
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
        'application/json': components['schemas']['ManualFinalizationInput']
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
  get_graph_api_branches__branch_id__story_graph_get: {
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
  create_record_api_branches__branch_id__story_records_post: {
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
        'application/json': components['schemas']['SaveRecord']
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
  update_record_api_branches__branch_id__story_records__identifier__put: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
        identifier: string
      }
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['SaveRecord']
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
  history_api_branches__branch_id__story_records__identifier__history_get: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
        identifier: string
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
  at_time_api_branches__branch_id__story_context_get: {
    parameters: {
      query?: {
        chapter_id?: string | null
        story_order?: number | null
        audience?: 'author' | 'character' | 'reader'
        character_id?: string | null
        text_position?: number
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
  preview_api_branches__branch_id__story_inheritance_get: {
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
  whole_context_api_branches__branch_id__chapters__chapter_id__scene_context_get: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
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
  history_api_branches__branch_id__chapters__chapter_id__scene_times__identifier__history_get: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
        chapter_id: string
        identifier: string
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
  get_scenes_api_branches__branch_id__chapters__chapter_id__scene_times_get: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
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
  create_api_branches__branch_id__chapters__chapter_id__scene_times_post: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
        chapter_id: string
      }
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['SaveScene']
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
  update_api_branches__branch_id__chapters__chapter_id__scene_times__identifier__put: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
        chapter_id: string
        identifier: string
      }
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['SaveScene']
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
  save_draft_api_branches__branch_id__import_previews__identifier__draft_patch: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
        identifier: string
      }
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['EditDraft']
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
  previews_api_branches__branch_id__import_previews_get: {
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
  preview_api_branches__branch_id__import_previews_post: {
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
        'multipart/form-data': components['schemas']['Body_preview_api_branches__branch_id__import_previews_post']
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
  get_preview_api_branches__branch_id__import_previews__identifier__get: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
        identifier: string
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
  read_segment_api_branches__branch_id__import_previews__identifier__read_post: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
        identifier: string
      }
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['Segment']
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
  confirm_api_branches__branch_id__import_previews__identifier__confirm_post: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
        identifier: string
      }
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['Confirm']
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
  genre_catalog_api_genre_catalog_get: {
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
  genre_profile_api_branches__branch_id__genre_profile_get: {
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
  preview_genre_profile_api_genre_profile_preview_post: {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['DefinitionInput']
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
  author_contract_api_branches__branch_id__author_contract_get: {
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
  preview_author_contract_api_author_contract_preview_post: {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['DefinitionInput']
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
  continue_with_account_api_tasks__task_id__continue_post: {
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
        'application/json': components['schemas']['ContinuationInput']
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
        text_position?: number
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
  references_api_reference_sessions_get: {
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
  create_reference_api_reference_sessions_post: {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['CreateReference']
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
  get_reference_api_reference_sessions__identifier__get: {
    parameters: {
      query?: never
      header?: never
      path: {
        identifier: string
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
  edit_reference_api_reference_sessions__identifier__patch: {
    parameters: {
      query?: never
      header?: never
      path: {
        identifier: string
      }
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['EditReference']
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
  reference_tasks_api_reference_sessions__identifier__tasks_get: {
    parameters: {
      query?: never
      header?: never
      path: {
        identifier: string
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
  start_reference_api_reference_sessions__identifier__actions_post: {
    parameters: {
      query?: never
      header?: never
      path: {
        identifier: string
      }
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['ReferenceAction']
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
  feedback_api_reference_sessions__identifier__feedback_post: {
    parameters: {
      query?: never
      header?: never
      path: {
        identifier: string
      }
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['ReferenceFeedback']
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
  history_api_branches__branch_id__story_extractions_get: {
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
  start_api_branches__branch_id__story_extractions_post: {
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
        'application/json': components['schemas']['StartExtraction']
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
  confirm_api_branches__branch_id__story_extractions__artifact_id__adopt_post: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
        artifact_id: string
      }
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['AdoptExtraction']
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
}
