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
  '/api/branches/{branch_id}/reading-metrics': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Latest Reading Metrics */
    get: operations['latest_reading_metrics_api_branches__branch_id__reading_metrics_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/reading-metrics/history': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Reading History */
    get: operations['reading_history_api_branches__branch_id__reading_metrics_history_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/reading-metrics/history/{identifier}': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Read Saved Report */
    get: operations['read_saved_report_api_branches__branch_id__reading_metrics_history__identifier__get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/reading-metrics/import': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Import Reading Metrics */
    post: operations['import_reading_metrics_api_branches__branch_id__reading_metrics_import_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/book-completion': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Book Completion */
    get: operations['book_completion_api_branches__branch_id__book_completion_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/book-completion/preview': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Preview Completion */
    post: operations['preview_completion_api_branches__branch_id__book_completion_preview_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/book-completion/confirm': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Confirm Completion */
    post: operations['confirm_completion_api_branches__branch_id__book_completion_confirm_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/book-completion/{identifier}/export': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Export Completion */
    get: operations['export_completion_api_branches__branch_id__book_completion__identifier__export_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/workflow-inspections': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Inspections */
    get: operations['inspections_api_branches__branch_id__workflow_inspections_get']
    put?: never
    /** Inspect */
    post: operations['inspect_api_branches__branch_id__workflow_inspections_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/workflow-inspections/length-targets': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    /** Save Length Targets */
    put: operations['save_length_targets_api_branches__branch_id__workflow_inspections_length_targets_put']
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/workflow-inspections/{inspection_id}': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Inspection */
    get: operations['inspection_api_branches__branch_id__workflow_inspections__inspection_id__get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/manuscript-package': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Get Package */
    get: operations['get_package_api_branches__branch_id__manuscript_package_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/manuscript-packages/import': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Import Package */
    post: operations['import_package_api_manuscript_packages_import_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/manuscript-evidence': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Get Evidence */
    get: operations['get_evidence_api_branches__branch_id__manuscript_evidence_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/manuscript-sources/{revision_id}': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Get Source */
    get: operations['get_source_api_branches__branch_id__manuscript_sources__revision_id__get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/work-snapshots': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** List Work Snapshots */
    get: operations['list_work_snapshots_api_branches__branch_id__work_snapshots_get']
    put?: never
    /** Save Work Snapshot */
    post: operations['save_work_snapshot_api_branches__branch_id__work_snapshots_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/work-snapshots/{snapshot_id}/preview': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Preview Work Snapshot */
    get: operations['preview_work_snapshot_api_branches__branch_id__work_snapshots__snapshot_id__preview_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/work-snapshots/{snapshot_id}/restore': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Restore Work Snapshot */
    post: operations['restore_work_snapshot_api_branches__branch_id__work_snapshots__snapshot_id__restore_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/work-snapshots/{snapshot_id}/chapters/{chapter_id}': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Diff Work Snapshot Chapter */
    get: operations['diff_work_snapshot_chapter_api_branches__branch_id__work_snapshots__snapshot_id__chapters__chapter_id__get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/desktop-sync': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Sync Status */
    get: operations['sync_status_api_branches__branch_id__desktop_sync_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/desktop-sync/enable': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Enable Sync */
    post: operations['enable_sync_api_branches__branch_id__desktop_sync_enable_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/desktop-sync/disable': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Disable Sync */
    post: operations['disable_sync_api_branches__branch_id__desktop_sync_disable_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/desktop-sync/run': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Run Sync */
    post: operations['run_sync_api_branches__branch_id__desktop_sync_run_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/desktop-sync/conflicts/{conflict_id}/compare': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Compare Sync */
    post: operations['compare_sync_api_branches__branch_id__desktop_sync_conflicts__conflict_id__compare_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/desktop-sync/conflicts/{conflict_id}/resolve': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Resolve Sync */
    post: operations['resolve_sync_api_branches__branch_id__desktop_sync_conflicts__conflict_id__resolve_post']
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
  '/api/branches/{branch_id}/outline-workspace': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Outline Workspace */
    get: operations['outline_workspace_api_branches__branch_id__outline_workspace_get']
    /** Save Workspace */
    put: operations['save_workspace_api_branches__branch_id__outline_workspace_put']
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/outline-workspace/confirm': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Confirm Workspace */
    post: operations['confirm_workspace_api_branches__branch_id__outline_workspace_confirm_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/outline-workspace/revisions/{revision}': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Outline Revision */
    get: operations['outline_revision_api_branches__branch_id__outline_workspace_revisions__revision__get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/outline-workspace/setting-options': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Setting Options */
    get: operations['setting_options_api_branches__branch_id__outline_workspace_setting_options_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/outline-workspace/reference-options': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Reference Options */
    get: operations['reference_options_api_branches__branch_id__outline_workspace_reference_options_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/ideas/adopt': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Adopt */
    post: operations['adopt_api_branches__branch_id__ideas_adopt_post']
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
  '/api/branches/{branch_id}/chapters/{chapter_id}/scene-times/{identifier}/reconfirmation-preview': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Read Preview */
    get: operations['read_preview_api_branches__branch_id__chapters__chapter_id__scene_times__identifier__reconfirmation_preview_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/chapters/{chapter_id}/scene-times/{identifier}/reconfirm': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Author Reconfirm */
    post: operations['author_reconfirm_api_branches__branch_id__chapters__chapter_id__scene_times__identifier__reconfirm_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/chapters/{chapter_id}/scene-times/{identifier}/reconfirmations/{key}': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Read Receipt */
    get: operations['read_receipt_api_branches__branch_id__chapters__chapter_id__scene_times__identifier__reconfirmations__key__get']
    put?: never
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
  '/api/branches/{branch_id}/conception/project-brief': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Get Project */
    get: operations['get_project_api_branches__branch_id__conception_project_brief_get']
    /** Confirm Project */
    put: operations['confirm_project_api_branches__branch_id__conception_project_brief_put']
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/conception': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Get Workspace */
    get: operations['get_workspace_api_branches__branch_id__conception_get']
    /** Save Workspace */
    put: operations['save_workspace_api_branches__branch_id__conception_put']
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/conception/adopt': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Adopt Candidate */
    post: operations['adopt_candidate_api_branches__branch_id__conception_adopt_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/conception/confirm': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Confirm Workspace */
    post: operations['confirm_workspace_api_branches__branch_id__conception_confirm_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/conception/generate': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Generate Conception */
    post: operations['generate_conception_api_branches__branch_id__conception_generate_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/conception/project-brief/generate': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Generate Project */
    post: operations['generate_project_api_branches__branch_id__conception_project_brief_generate_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/conception/extract-world': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Extract Conception World */
    post: operations['extract_conception_world_api_branches__branch_id__conception_extract_world_post']
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
  '/api/tasks/{task_id}/continue-conditional-policy': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Continue Conditional Policy */
    post: operations['continue_conditional_policy_api_tasks__task_id__continue_conditional_policy_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/tasks/{task_id}/reprepare-plan-preview': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Reprepare Plan Preview */
    get: operations['reprepare_plan_preview_api_tasks__task_id__reprepare_plan_preview_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/tasks/{task_id}/plan-restarts/{client_request_id}': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Plan Restart Receipt */
    get: operations['plan_restart_receipt_api_tasks__task_id__plan_restarts__client_request_id__get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/tasks/{task_id}/reprepare-plan': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Reprepare Plan */
    post: operations['reprepare_plan_api_tasks__task_id__reprepare_plan_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/artifacts/{artifact_id}/source-interpretation-correction-preview': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Correction Preview */
    get: operations['correction_preview_api_artifacts__artifact_id__source_interpretation_correction_preview_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/artifacts/{artifact_id}/source-interpretation-corrections': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Create Correction */
    post: operations['create_correction_api_artifacts__artifact_id__source_interpretation_corrections_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/artifacts/{artifact_id}/source-interpretation-corrections/{client_request_id}': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Correction Request */
    get: operations['correction_request_api_artifacts__artifact_id__source_interpretation_corrections__client_request_id__get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/artifacts/{artifact_id}/recheck-source-correction-preview': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Recheck Preview */
    get: operations['recheck_preview_api_artifacts__artifact_id__recheck_source_correction_preview_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/artifacts/{artifact_id}/recheck-source-correction': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Create Recheck */
    post: operations['create_recheck_api_artifacts__artifact_id__recheck_source_correction_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/artifacts/{artifact_id}/recheck-source-correction/{client_request_id}': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Recheck Request */
    get: operations['recheck_request_api_artifacts__artifact_id__recheck_source_correction__client_request_id__get']
    put?: never
    post?: never
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
  '/api/artifacts/{artifact_id}/history': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /**
     * Candidate History
     * @description 分页返回候选编辑与复检前的只读快照；旧事件缺失的内容不推造。
     */
    get: operations['candidate_history_api_artifacts__artifact_id__history_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
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
  '/api/artifacts/{artifact_id}/acceptance': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Candidate Acceptance Receipt */
    get: operations['candidate_acceptance_receipt_api_artifacts__artifact_id__acceptance_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/artifacts/{artifact_id}/recheck': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /**
     * Recheck Candidate
     * @description 按原写前冻结契约复检待审候选，不重新解释情节、不采纳或恢复原流程。
     */
    post: operations['recheck_candidate_api_artifacts__artifact_id__recheck_post']
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
    /**
     * Books
     * @description 返回导入时资料记录；file_hash是导入文件SHA，原件变化不会自动重读。同名再导入建立独立资料。
     */
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
    /**
     * Search
     * @description 只检索导入时正文快照；命中file_hash为导入文件身份，不表示已核对当前磁盘原件。
     */
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
    /**
     * Units
     * @description 返回导入时正文快照的单元目录，不因外部原件变化自动重解析。
     */
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
    /**
     * Unit
     * @description content是导入时正文快照，file_hash是导入文件SHA；不保证当前原件仍存在或未改变。
     */
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
    /** Edit Saved Reference */
    patch: operations['edit_saved_reference_api_reference_sessions__identifier__patch']
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
  '/api/reference-sessions/{identifier}/cards/{card_id}/revisions/{revision}': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Get Card */
    get: operations['get_card_api_reference_sessions__identifier__cards__card_id__revisions__revision__get']
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
  '/api/branches/{branch_id}/outline-workspace/generation': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** History */
    get: operations['history_api_branches__branch_id__outline_workspace_generation_get']
    put?: never
    /** Start */
    post: operations['start_api_branches__branch_id__outline_workspace_generation_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/outline-workspace/generation/{identifier}': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Proposal */
    get: operations['proposal_api_branches__branch_id__outline_workspace_generation__identifier__get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/workflow-inspections/semantic': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Start */
    post: operations['start_api_branches__branch_id__workflow_inspections_semantic_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/workflow-inspections/semantic/tasks': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** History */
    get: operations['history_api_branches__branch_id__workflow_inspections_semantic_tasks_get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/workflow-inspections/semantic/{identifier}': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Candidate */
    get: operations['candidate_api_branches__branch_id__workflow_inspections_semantic__identifier__get']
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/book-completion/preparation': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Latest Preparation */
    get: operations['latest_preparation_api_branches__branch_id__book_completion_preparation_get']
    put?: never
    /** Prepare */
    post: operations['prepare_api_branches__branch_id__book_completion_preparation_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/seven-stage-runs': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    /** Runs */
    get: operations['runs_api_branches__branch_id__seven_stage_runs_get']
    put?: never
    /** Create Run */
    post: operations['create_run_api_branches__branch_id__seven_stage_runs_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/seven-stage-runs/{task_id}/pause': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Pause Run */
    post: operations['pause_run_api_branches__branch_id__seven_stage_runs__task_id__pause_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/seven-stage-runs/{task_id}/resume': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Resume Run */
    post: operations['resume_run_api_branches__branch_id__seven_stage_runs__task_id__resume_post']
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/branches/{branch_id}/seven-stage-runs/{task_id}/cancel': {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    get?: never
    put?: never
    /** Cancel Run */
    post: operations['cancel_run_api_branches__branch_id__seven_stage_runs__task_id__cancel_post']
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
      /**
       * Memory Fact Indices
       * @description 仅正文/局部替换候选：None沿用全选，[]明确不采用附带事实；编号来自原memory.facts顺序。
       */
      memory_fact_indices?: number[] | null
      /**
       * Memory Selection Reason
       * @default
       */
      memory_selection_reason: string
      /** Memory Facts Sha256 */
      memory_facts_sha256?: string | null
    }
    /** AdoptConception */
    AdoptConception: {
      /** Base Revision */
      base_revision: number
      /** Base Story Revision */
      base_story_revision: number
      /** Idempotency Key */
      idempotency_key: string
      /** Artifact Id */
      artifact_id: string
      /** Categories */
      categories: components['schemas']['CandidateSelection'][]
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
    /** AggregateCount */
    AggregateCount: {
      /** Chapter Id */
      chapter_id: string
      /** Unique Readers */
      unique_readers: number | null
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
    /** AuthorReconfirmation */
    AuthorReconfirmation: {
      /**
       * Version
       * @default scene-author-reconfirmation/1
       * @constant
       */
      version: 'scene-author-reconfirmation/1'
      /**
       * Authority
       * @default author_confirmation_not_model_review
       * @constant
       */
      authority: 'author_confirmation_not_model_review'
      /** Client Request Id */
      client_request_id: string
      /** Request Sha256 */
      request_sha256: string
      /** Reason */
      reason: string
      /** Confirmed At */
      confirmed_at: number
      /** Source Revision */
      source_revision: number
      /** Content Sha256 */
      content_sha256: string
      /** Previous Scene Revision */
      previous_scene_revision: number
      /** Scene Revision */
      scene_revision: number
      /** Base Story Revision */
      base_story_revision: number
      /** Story Revision */
      story_revision: number
      /** Previous Payload Sha256 */
      previous_payload_sha256: string
      /** Previous Raw Json Sha256 */
      previous_raw_json_sha256: string
      /** Previous Story Time */
      previous_story_time: string
      /** Previous Refs */
      previous_refs: components['schemas']['EventWitness'][]
      /** Current Refs */
      current_refs: components['schemas']['EventWitness'][]
      /** Time Evidence */
      time_evidence?: components['schemas']['Source'][]
    }
    /** AuthorReview */
    AuthorReview: {
      /**
       * Check Id
       * @enum {string}
       */
      check_id: '01' | '02' | '03' | '04' | '05' | '06' | '07' | '08' | '09' | '10'
      /**
       * Status
       * @enum {string}
       */
      status: 'green' | 'yellow' | 'red' | 'not_applicable'
      /** Basis Note */
      basis_note: string
      /** Reason */
      reason: string
      /** Evidence */
      evidence?: components['schemas']['Source'][]
      /** Source Candidate Id */
      source_candidate_id?: string | null
      /**
       * Author Overrides Candidate
       * @default false
       */
      author_overrides_candidate: boolean
    }
    /** BasisExcerpt */
    BasisExcerpt: {
      /** Id */
      id: string
      /** Label */
      label: string
      /** Text */
      text: string
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
    /** Body_import_reading_metrics_api_branches__branch_id__reading_metrics_import_post */
    Body_import_reading_metrics_api_branches__branch_id__reading_metrics_import_post: {
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
    /** CandidateItem */
    CandidateItem: {
      /**
       * Check Id
       * @enum {string}
       */
      check_id: '01' | '02' | '10'
      /**
       * Result
       * @enum {string}
       */
      result: 'pass' | 'fail' | 'unknown'
      /** Reason */
      reason: string
      /** Evidence */
      evidence: components['schemas']['Source'][]
      /** Basis Excerpts */
      basis_excerpts: components['schemas']['BasisExcerpt'][]
    }
    /** CandidateRecheckInput */
    CandidateRecheckInput: {
      /**
       * Source Identity Context
       * @default false
       */
      source_identity_context: boolean
      /** Candidate Revision */
      candidate_revision: number
      /** Account */
      account: string
      /** Client Request Id */
      client_request_id: string
      /** Budget Limit */
      budget_limit?: number | null
    }
    /** CandidateSelection */
    CandidateSelection: {
      /** Id */
      id: string
      /** Items */
      items: components['schemas']['ConceptionItem'][]
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
    /** ChapterPlan */
    ChapterPlan: {
      /** Chapter Id */
      chapter_id: string
      /** Goal */
      goal: string
      /** Obstacle */
      obstacle: string
      /** Turn */
      turn: string
      /** Result */
      result: string
      /** Setting Ids */
      setting_ids?: string[]
      /** Reference Cards */
      reference_cards?: components['schemas']['OutlineCardReference'][]
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
    /** ClosureReview */
    ClosureReview: {
      /** Note */
      note: string
      /** Sources */
      sources?: components['schemas']['Source'][]
    }
    /** Cohort */
    Cohort: {
      /**
       * Started From
       * Format: date-time
       */
      started_from: string
      /**
       * Started Before
       * Format: date-time
       */
      started_before: string
      /** Channel */
      channel?: string | null
    }
    /** CompletionRequest */
    CompletionRequest: {
      /** Basis Sha256 */
      basis_sha256: string
      /** Read Note */
      read_note: string
      ending: components['schemas']['ClosureReview']
      characters: components['schemas']['ClosureReview']
      promises: components['schemas']['ClosureReview']
      /** Intro */
      intro: string
      /**
       * Spoiler Scope
       * @enum {string}
       */
      spoiler_scope: 'opening' | 'whole'
      /** Sample Chapter Ids */
      sample_chapter_ids: string[]
      /** Materials Reviewed */
      materials_reviewed: boolean
      /** Preparation Id */
      preparation_id?: string | null
    }
    /** ConceptionCategory */
    ConceptionCategory: {
      /** Id */
      id: string
      /** Title */
      title: string
      /** Items */
      items?: components['schemas']['ConceptionItem'][]
      /**
       * Skipped
       * @default false
       */
      skipped: boolean
    }
    /** ConceptionConfirmation */
    ConceptionConfirmation: {
      /** Id */
      id: string
      /** Revision */
      revision: number
      /** Current */
      current: boolean
      /** Note */
      note: string
      /** Planning Brief */
      planning_brief: string
    }
    /** ConceptionGenerateInput */
    ConceptionGenerateInput: {
      /** Base Revision */
      base_revision: number
      /** Base Story Revision */
      base_story_revision: number
      /** Account */
      account: string
      /** Client Request Id */
      client_request_id: string
      /** Budget Limit */
      budget_limit?: number | null
      /** Category Id */
      category_id?: string | null
      /**
       * Instruction
       * @default
       */
      instruction: string
      /** Project Confirmation Id */
      project_confirmation_id?: string | null
    }
    /** ConceptionIssue */
    ConceptionIssue: {
      /** Category Id */
      category_id: string
      /** Message */
      message: string
    }
    /** ConceptionItem */
    ConceptionItem: {
      /** Id */
      id: string
      /** Title */
      title: string
      /**
       * Detail
       * @default
       */
      detail: string
      /** Links */
      links?: string[]
      setting_policy?: components['schemas']['SettingPolicy'] | null
    }
    /** ConceptionObserved */
    ConceptionObserved: {
      /** Id */
      id: string
      /** Category Id */
      category_id: string
      /** Title */
      title: string
      /** Revision */
      revision: number
      /** Description */
      description: string
    }
    /** ConceptionProjectImpact */
    ConceptionProjectImpact: {
      /** Item Id */
      item_id: string
      /** Category Id */
      category_id: string
      /** Title */
      title: string
      /** Source Project Id */
      source_project_id: string
      /** Source Project Revision */
      source_project_revision?: number | null
      /** Reconciled */
      reconciled: boolean
    }
    /** ConceptionSource */
    ConceptionSource: {
      /** Item Id */
      item_id: string
      evidence: components['schemas']['Source']
      /** Current */
      current: boolean
      /** Author Edited */
      author_edited: boolean
    }
    /** ConceptionWorkspace */
    ConceptionWorkspace: {
      /** Revision */
      revision: number
      /** Story Revision */
      story_revision: number
      /** Categories */
      categories: components['schemas']['ConceptionCategory'][]
      /** Issues */
      issues: components['schemas']['ConceptionIssue'][]
      confirmation: components['schemas']['ConceptionConfirmation'] | null
      /** Observed */
      observed: components['schemas']['ConceptionObserved'][]
      /** Sources */
      sources: components['schemas']['ConceptionSource'][]
      project?: components['schemas']['ConfirmedProject'] | null
      /** Project Impacts */
      project_impacts?: components['schemas']['ConceptionProjectImpact'][]
    }
    /** ConceptionWorldExtractInput */
    ConceptionWorldExtractInput: {
      /** Base Revision */
      base_revision: number
      /** Base Story Revision */
      base_story_revision: number
      /** Account */
      account: string
      /** Client Request Id */
      client_request_id: string
      /** Budget Limit */
      budget_limit?: number | null
      /** Chapter Ids */
      chapter_ids: string[]
    }
    /** ConditionalContinuationInput */
    ConditionalContinuationInput: {
      /** Client Request Id */
      client_request_id: string
      /** Reason */
      reason: string
      /** Account */
      account?: string | null
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
    /** ConfirmConception */
    ConfirmConception: {
      /** Base Revision */
      base_revision: number
      /** Base Story Revision */
      base_story_revision: number
      /** Idempotency Key */
      idempotency_key: string
      /**
       * Note
       * @default
       */
      note: string
      /** Reconciled Project Items */
      reconciled_project_items?: string[]
    }
    /** ConfirmOutline */
    ConfirmOutline: {
      /** Base Revision */
      base_revision: number
    }
    /** ConfirmProject */
    ConfirmProject: {
      /** Base Revision */
      base_revision: number
      /** Idempotency Key */
      idempotency_key: string
      /** Idea */
      idea: string
      brief: components['schemas']['ProjectBrief']
      /** Candidate Id */
      candidate_id?: string | null
    }
    /** ConfirmedProject */
    ConfirmedProject: {
      /** Id */
      id: string
      /** Revision */
      revision: number
      /** Idea */
      idea: string
      brief: components['schemas']['ProjectBrief']
      /** Candidate Id */
      candidate_id: string | null
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
    /** CorrectionInput */
    CorrectionInput: {
      /**
       * Source Identity Context
       * @default false
       * @constant
       */
      source_identity_context: false
      /** Candidate Revision */
      candidate_revision: number
      /** Account */
      account: string
      /** Client Request Id */
      client_request_id: string
      /** Budget Limit */
      budget_limit?: number | null
      /** Preview Basis Sha256 */
      preview_basis_sha256: string
      /** Item Ids */
      item_ids: string[]
      /**
       * Reason
       * @default
       */
      reason: string
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
    /** CurrentEventWitness */
    CurrentEventWitness: {
      /** Ref */
      ref: string
      /** Event Id */
      event_id: string
      /** Event Revision */
      event_revision: number
      /** Story Order */
      story_order: number
      /** Record Sha256 */
      record_sha256: string
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
      /** Pinned Revisions */
      pinned_revisions?: {
        [key: string]: number
      }
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
    /** EvidenceChapter */
    EvidenceChapter: {
      /** Id */
      id: string
      /** Revision */
      revision: number
      /** Sha256 */
      sha256: string
      /** Revision Id */
      revision_id?: string | null
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
    /** GenerateInspectionCandidate */
    GenerateInspectionCandidate: {
      /**
       * Scope
       * @default book
       * @enum {string}
       */
      scope: 'chapter' | 'volume' | 'book'
      /** Scope Id */
      scope_id?: string | null
      /** Basis Sha256 */
      basis_sha256: string
      /** Account */
      account: string
      /** Client Request Id */
      client_request_id: string
      /** Budget Limit */
      budget_limit?: number | null
    }
    /** GenerateOutline */
    GenerateOutline: {
      /** Base Revision */
      base_revision: number
      /** Confirmation Id */
      confirmation_id?: string | null
      /** Account */
      account: string
      /**
       * Instruction
       * @default
       */
      instruction: string
      /** Client Request Id */
      client_request_id: string
      /** Budget Limit */
      budget_limit?: number | null
      /** Reference Cards */
      reference_cards?: components['schemas']['OutlineCardReference'][]
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
    /** IdeaAdoptInput */
    IdeaAdoptInput: {
      /** Text */
      text: string
      /** Author Idea */
      author_idea: string
      /** Source Candidate Id */
      source_candidate_id?: string | null
      /**
       * Candidate Revision
       * @default 0
       */
      candidate_revision: number
      /** Base Story Revision */
      base_story_revision: number
      /** Idempotency Key */
      idempotency_key: string
    }
    /** ImportedReport */
    ImportedReport: {
      /** Id */
      id: string
      report: components['schemas']['ReadingReport']
    }
    /** InspectedChapter */
    InspectedChapter: {
      /** Id */
      id: string
      /** Title */
      title: string
      /** Revision */
      revision: number
      /** Sha256 */
      sha256: string
    }
    /** InspectionCandidate */
    InspectionCandidate: {
      /** Id */
      id: string
      /**
       * Scope
       * @enum {string}
       */
      scope: 'chapter' | 'volume' | 'book'
      /** Scope Id */
      scope_id: string | null
      /** Basis Sha256 */
      basis_sha256: string
      /** Current */
      current: boolean
      /** Chapters */
      chapters: components['schemas']['InspectedChapter'][]
      /** Items */
      items: components['schemas']['CandidateItem'][]
    }
    /** InspectionCandidateTask */
    InspectionCandidateTask: {
      /** Id */
      id: string
      /** Status */
      status: string
      /** Error */
      error: string
      /** Created */
      created: number
      /** Artifact Id */
      artifact_id: string | null
    }
    /** InspectionInput */
    InspectionInput: {
      /**
       * Scope
       * @default book
       * @enum {string}
       */
      scope: 'chapter' | 'volume' | 'book'
      /** Scope Id */
      scope_id?: string | null
      /** Basis Sha256 */
      basis_sha256: string
      /** Author Reviews */
      author_reviews?: components['schemas']['AuthorReview'][]
      length_targets?: components['schemas']['LengthTargets'] | null
    }
    /** LengthTargets */
    LengthTargets: {
      /** Min Chars */
      min_chars?: number | null
      /** Max Chars */
      max_chars?: number | null
      /** Expected Chapters */
      expected_chapters?: number | null
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
    /** ManuscriptPackage */
    ManuscriptPackage: {
      /**
       * Format
       * @constant
       */
      format: 'ainovel-manuscript/1'
      novel: components['schemas']['PortableWork']
      branch: components['schemas']['PortableBranch']
      /** Chapters */
      chapters: components['schemas']['PortableChapter'][]
      /** Revisions */
      revisions: components['schemas']['PortableRevision'][]
      /** Evidence */
      evidence?: components['schemas']['PortableEvidence'][]
      /** Source Revisions */
      source_revisions?: components['schemas']['PortableRevision'][]
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
    /** Metric */
    Metric: {
      /**
       * Kind
       * @enum {string}
       */
      kind: 'adjacent_follow' | 'first_chapter_retention' | 'chapter_completion' | 'aggregate_ratio'
      /** Chapter Id */
      chapter_id: string
      /** Target Chapter Id */
      target_chapter_id?: string | null
      /** Numerator */
      numerator: number | null
      /** Denominator */
      denominator: number | null
      /** Rate */
      rate: number | null
      /**
       * Excluded Incomplete
       * @default 0
       */
      excluded_incomplete: number
      /**
       * Excluded Prior Or Tied
       * @default 0
       */
      excluded_prior_or_tied: number
    }
    /** ModuleInput */
    ModuleInput: {
      /**
       * Plot Contract Check
       * @default false
       */
      plot_contract_check: boolean
      /**
       * Source Identity Context
       * @default false
       */
      source_identity_context: boolean
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
        | 'ideation.structure'
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
      /** Conception Snapshot Id */
      conception_snapshot_id?: string | null
      /** Setting Item Id */
      setting_item_id?: string | null
      /** Outline Revision Id */
      outline_revision_id?: string | null
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
    /** NativeMetric */
    NativeMetric: {
      /** Name */
      name: string
      /** Definition */
      definition: string
      /** Value */
      value: number
      /** Unit */
      unit: string
      /** Chapter Id */
      chapter_id?: string | null
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
    /** OutlineCard */
    OutlineCard: {
      /** Session Id */
      session_id: string
      /** Card Id */
      card_id: string
      /** Revision */
      revision: number
      /** Technique */
      technique: string
      /** Conditions */
      conditions: string
      /** Exceptions */
      exceptions: string
      /** Source Title */
      source_title: string
    }
    /** OutlineCardReference */
    OutlineCardReference: {
      /** Session Id */
      session_id: string
      /** Card Id */
      card_id: string
      /** Revision */
      revision: number
    }
    /** OutlineGenerationTask */
    OutlineGenerationTask: {
      /** Id */
      id: string
      /** Status */
      status: string
      /** Error */
      error: string
      /** Created */
      created: number
      /** Artifact Id */
      artifact_id: string | null
    }
    /** OutlinePlan */
    OutlinePlan: {
      /** Summary */
      summary: string
      /** Volumes */
      volumes?: components['schemas']['VolumePlan'][]
      /** Promises */
      promises?: components['schemas']['PromiseWindow'][]
    }
    /** OutlineProposal */
    OutlineProposal: {
      /** Id */
      id: string
      /** Base Revision */
      base_revision: number
      /** Confirmation Id */
      confirmation_id: string | null
      /** Project Revision */
      project_revision: number | null
      /** Current */
      current: boolean
      plan: components['schemas']['OutlinePlan']
    }
    /** PlanRestartInput */
    PlanRestartInput: {
      /** Client Request Id */
      client_request_id: string
      /** Reason */
      reason: string
      /** Chapter */
      chapter: number
      /** Base Story Revision */
      base_story_revision: number
      /** Base Progress Sha256 */
      base_progress_sha256: string
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
    /** PortableBranch */
    PortableBranch: {
      /** Id */
      id: string
      /** Novel Id */
      novel_id: string
      /** Name */
      name: string
      /**
       * Mode
       * @enum {string}
       */
      mode: 'advisory' | 'assisted' | 'automatic'
      /** Story Revision */
      story_revision: number
      /** Created */
      created: number
    }
    /** PortableChapter */
    PortableChapter: {
      /** Id */
      id: string
      /** Branch Id */
      branch_id: string
      /** Position */
      position: number
      /** Title */
      title: string
      /** Revision */
      revision: number
      /**
       * Archived
       * @enum {integer}
       */
      archived: 0 | 1
      /**
       * Stale
       * @enum {integer}
       */
      stale: 0 | 1
    }
    /** PortableEvidence */
    PortableEvidence: {
      /** Id */
      id: string
      /**
       * Kind
       * @enum {string}
       */
      kind: 'project' | 'settings' | 'outline' | 'inspection' | 'completion'
      /** Title */
      title: string
      /** Body */
      body: string
      /** Sha256 */
      sha256: string
      /** Chapters */
      chapters?: components['schemas']['EvidenceChapter'][]
    }
    /** PortableRevision */
    PortableRevision: {
      /** Id */
      id: string
      /** Chapter Id */
      chapter_id: string
      /** Revision */
      revision: number
      /** Parent Id */
      parent_id: string | null
      /** Content */
      content: string
      /** Sha256 */
      sha256: string
      /** Cause */
      cause: string
      /** Created */
      created: number
    }
    /** PortableWork */
    PortableWork: {
      /** Id */
      id: string
      /** Title */
      title: string
      /** Brief */
      brief: string
      /** Created */
      created: number
    }
    /** PrepareCompletion */
    PrepareCompletion: {
      /** Basis Sha256 */
      basis_sha256: string
      /**
       * Spoiler Scope
       * @default opening
       * @enum {string}
       */
      spoiler_scope: 'opening' | 'whole'
      /**
       * Sample Chapters
       * @default 1
       */
      sample_chapters: number
      /** Account */
      account: string
      /** Client Request Id */
      client_request_id: string
      /** Budget Limit */
      budget_limit?: number | null
    }
    /** ProjectBrief */
    ProjectBrief: {
      /** Position */
      position: string
      /** Track */
      track: string
      /** Genre Ids */
      genre_ids: string[]
      /** Audience */
      audience: string
      /** Distinction */
      distinction: string
      /** Length */
      length: string
      /**
       * Cadence
       * @default
       */
      cadence: string
      /** Ending */
      ending: string
      /**
       * Final Image
       * @default
       */
      final_image: string
    }
    /** ProjectGenerateInput */
    ProjectGenerateInput: {
      /** Base Revision */
      base_revision: number
      /** Base Story Revision */
      base_story_revision: number
      /** Account */
      account: string
      /** Client Request Id */
      client_request_id: string
      /** Budget Limit */
      budget_limit?: number | null
      /** Idea */
      idea: string
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
    /** PromiseWindow */
    PromiseWindow: {
      /** Promise Id */
      promise_id: string
      /** Plant Chapter Id */
      plant_chapter_id: string
      /** Opens Chapter Id */
      opens_chapter_id: string
      /** Closes Chapter Id */
      closes_chapter_id: string
    }
    /** PublicationChange */
    PublicationChange: {
      /** Chapter Id */
      chapter_id: string
      /**
       * Kind
       * @enum {string}
       */
      kind: 'access' | 'recommendation' | 'revision'
      /**
       * At
       * Format: date-time
       */
      at: string
      /** Note */
      note: string
    }
    /** PublishedChapter */
    PublishedChapter: {
      /** Chapter Id */
      chapter_id: string
      /**
       * Published At
       * Format: date-time
       */
      published_at: string
    }
    /** ReadingDiagnosis */
    ReadingDiagnosis: {
      /** Chapter Id */
      chapter_id: string
      /** Target Chapter Id */
      target_chapter_id: string
      /** Rate */
      rate: number
      /** Numerator */
      numerator: number
      /** Denominator */
      denominator: number
      /** Baseline Rate */
      baseline_rate: number
      /** Baseline Pairs */
      baseline_pairs: number
      /** Publication Gap Hours */
      publication_gap_hours: number
      /** Median Publication Gap Hours */
      median_publication_gap_hours: number
      /** Changes */
      changes: components['schemas']['PublicationChange'][]
    }
    /** ReadingHistoryEntry */
    ReadingHistoryEntry: {
      /** Id */
      id: string
      /** Release Version */
      release_version: string
      /**
       * Mode
       * @enum {string}
       */
      mode: 'events' | 'aggregate'
      /**
       * Extracted At
       * Format: date-time
       */
      extracted_at: string
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
    /** ReadingMetricsState */
    ReadingMetricsState: {
      latest: components['schemas']['ImportedReport'] | null
    }
    /** ReadingReport */
    ReadingReport: {
      /**
       * Format
       * @constant
       */
      format: 'ainovel-reading/1'
      /**
       * Mode
       * @enum {string}
       */
      mode: 'events' | 'aggregate'
      /** Source */
      source: string
      /** Release Version */
      release_version: string
      /** Timezone */
      timezone: string
      /**
       * Extracted At
       * Format: date-time
       */
      extracted_at: string
      /**
       * Observed From
       * Format: date-time
       */
      observed_from: string
      /**
       * Observed Until
       * Format: date-time
       */
      observed_until: string
      /** Chapters */
      chapters: components['schemas']['PublishedChapter'][]
      /** Publication Changes */
      publication_changes?: components['schemas']['PublicationChange'][]
      /** Read Definition */
      read_definition: string
      /** Completion Definition */
      completion_definition?: string | null
      cohort?: components['schemas']['Cohort'] | null
      /** Window Hours */
      window_hours?: number | null
      /** Event Rows */
      event_rows?: number | null
      /** Distinct Readers */
      distinct_readers?: number | null
      /** Event Channels */
      event_channels?: string[]
      /** Counts */
      counts?: components['schemas']['AggregateCount'][]
      /** Native Metrics */
      native_metrics?: components['schemas']['NativeMetric'][]
      /** Metrics */
      metrics: components['schemas']['Metric'][]
      /** Diagnostics */
      diagnostics?: components['schemas']['ReadingDiagnosis'][]
      /** Notes */
      notes: string[]
    }
    /** RecheckCorrectionInput */
    RecheckCorrectionInput: {
      /**
       * Source Identity Context
       * @default false
       * @constant
       */
      source_identity_context: false
      /** Candidate Revision */
      candidate_revision: number
      /** Account */
      account: string
      /** Client Request Id */
      client_request_id: string
      /** Budget Limit */
      budget_limit?: number | null
      /** Preview Basis Sha256 */
      preview_basis_sha256: string
      /** Receipt Id */
      receipt_id: string
    }
    /** ReconfirmationInput */
    ReconfirmationInput: {
      /** Client Request Id */
      client_request_id: string
      /** Base Story Revision */
      base_story_revision: number
      /** Base Revision */
      base_revision: number
      /** Base Payload Sha256 */
      base_payload_sha256: string
      /** Source Revision */
      source_revision: number
      /** Content Sha256 */
      content_sha256: string
      /** Reason */
      reason: string
      /** Refs */
      refs: components['schemas']['CurrentEventWitness'][]
      /** Story Time */
      story_time: string
      /** Time Evidence */
      time_evidence?: components['schemas']['TimeEvidence'][]
    }
    /** ReferenceAction */
    ReferenceAction: {
      /**
       * Plot Contract Check
       * @default false
       */
      plot_contract_check: boolean
      /**
       * Source Identity Context
       * @default false
       */
      source_identity_context: boolean
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
      analysis_scope?: components['schemas']['ReferenceAnalysisScope'] | null
    }
    /** ReferenceAnalysisScope */
    ReferenceAnalysisScope: {
      /**
       * Start
       * @description 选段内的 UTF-16 起点
       */
      start: number
      /**
       * End
       * @description 选段内的 UTF-16 终点
       */
      end: number
      /** Card Id */
      card_id?: string | null
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
      /**
       * Pause After Maintenance
       * @default false
       */
      pause_after_maintenance: boolean
    }
    /** SaveConception */
    SaveConception: {
      /** Base Revision */
      base_revision: number
      /** Base Story Revision */
      base_story_revision: number
      /** Categories */
      categories: components['schemas']['ConceptionCategory'][]
    }
    /** SaveInput */
    SaveInput: {
      /** Base Revision */
      base_revision: number
      /** Content */
      content: string
    }
    /** SaveLengthTargets */
    SaveLengthTargets: {
      /**
       * Scope
       * @default book
       * @enum {string}
       */
      scope: 'chapter' | 'volume' | 'book'
      /** Scope Id */
      scope_id?: string | null
      /** Base Target Id */
      base_target_id?: string | null
      targets: components['schemas']['LengthTargets']
    }
    /** SaveOutline */
    SaveOutline: {
      /** Base Revision */
      base_revision: number
      /** Confirmation Id */
      confirmation_id?: string | null
      /** Source Generation Id */
      source_generation_id?: string | null
      plan: components['schemas']['OutlinePlan']
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
    /** SavedReferenceCard */
    SavedReferenceCard: {
      /** Session Id */
      session_id: string
      /** Revision */
      revision: number
      card: components['schemas']['ReferenceCard']
      /** Source */
      source: {
        [key: string]: unknown
      }
      /** Goal */
      goal: string | null
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
      author_reconfirmed?: components['schemas']['AuthorReconfirmation'] | null
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
    /** SettingPolicy */
    SettingPolicy: {
      /**
       * Strength
       * @enum {string}
       */
      strength: 'hard' | 'soft'
      /**
       * Scope
       * @default
       */
      scope: string
      /**
       * Exceptions
       * @default
       */
      exceptions: string
    }
    /** SnapshotInput */
    SnapshotInput: {
      /** Label */
      label: string
      /**
       * Scope
       * @default book
       * @enum {string}
       */
      scope: 'chapter' | 'volume' | 'book'
      /** Scope Id */
      scope_id?: string | null
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
    /** StartRun */
    StartRun: {
      /**
       * Start From
       * @enum {string}
       */
      start_from: 'idea' | 'settings' | 'outline'
      /** Source Id */
      source_id?: string | null
      /** Idea */
      idea?: string | null
      /** Chapter Count */
      chapter_count?: number | null
      /** Account */
      account: string
      /** Budget Limit */
      budget_limit: number
      /** Client Request Id */
      client_request_id: string
      /**
       * Auto Confirm Planning
       * @default false
       */
      auto_confirm_planning: boolean
      /**
       * Auto Adopt Body
       * @default false
       */
      auto_adopt_body: boolean
      /** Reference Session Id */
      reference_session_id?: string | null
      /** Reference Revision */
      reference_revision?: number | null
      /**
       * Auto Adopt Reference
       * @default false
       */
      auto_adopt_reference: boolean
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
    /** TimeEvidence */
    TimeEvidence: {
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
    /** UnitPlan */
    UnitPlan: {
      /** Id */
      id: string
      /** Title */
      title: string
      /** Summary */
      summary: string
      /** Chapters */
      chapters?: components['schemas']['ChapterPlan'][]
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
    /** VolumePlan */
    VolumePlan: {
      /** Id */
      id: string
      /** Title */
      title: string
      /** Summary */
      summary: string
      /** Units */
      units?: components['schemas']['UnitPlan'][]
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
       * Source Identity Context
       * @default false
       */
      source_identity_context: boolean
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
      /** Conception Snapshot Id */
      conception_snapshot_id?: string | null
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
    /** RestoreInput */
    ainovel__models__RestoreInput: {
      /** Base Revision */
      base_revision: number
      /** Revision Id */
      revision_id: string
    }
    /** RestoreInput */
    ainovel__work_snapshot__RestoreInput: {
      /** Basis Sha256 */
      basis_sha256: string
      /**
       * Mode
       * @enum {string}
       */
      mode: 'in_place' | 'new_branch'
      /** Branch Name */
      branch_name?: string | null
      /** Idempotency Key */
      idempotency_key: string
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
  latest_reading_metrics_api_branches__branch_id__reading_metrics_get: {
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
          'application/json': components['schemas']['ReadingMetricsState']
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
  reading_history_api_branches__branch_id__reading_metrics_history_get: {
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
          'application/json': components['schemas']['ReadingHistoryEntry'][]
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
  read_saved_report_api_branches__branch_id__reading_metrics_history__identifier__get: {
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
          'application/json': components['schemas']['ImportedReport']
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
  import_reading_metrics_api_branches__branch_id__reading_metrics_import_post: {
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
        'multipart/form-data': components['schemas']['Body_import_reading_metrics_api_branches__branch_id__reading_metrics_import_post']
      }
    }
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['ImportedReport']
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
  book_completion_api_branches__branch_id__book_completion_get: {
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
  preview_completion_api_branches__branch_id__book_completion_preview_post: {
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
        'application/json': components['schemas']['CompletionRequest']
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
  confirm_completion_api_branches__branch_id__book_completion_confirm_post: {
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
        'application/json': components['schemas']['CompletionRequest']
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
  export_completion_api_branches__branch_id__book_completion__identifier__export_get: {
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
  inspections_api_branches__branch_id__workflow_inspections_get: {
    parameters: {
      query?: {
        scope?: 'chapter' | 'volume' | 'book'
        scope_id?: string | null
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
  inspect_api_branches__branch_id__workflow_inspections_post: {
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
        'application/json': components['schemas']['InspectionInput']
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
  save_length_targets_api_branches__branch_id__workflow_inspections_length_targets_put: {
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
        'application/json': components['schemas']['SaveLengthTargets']
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
  inspection_api_branches__branch_id__workflow_inspections__inspection_id__get: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
        inspection_id: string
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
  get_package_api_branches__branch_id__manuscript_package_get: {
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
          'application/json': components['schemas']['ManuscriptPackage']
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
  import_package_api_manuscript_packages_import_post: {
    parameters: {
      query?: never
      header?: never
      path?: never
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['ManuscriptPackage']
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
  get_evidence_api_branches__branch_id__manuscript_evidence_get: {
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
          'application/json': components['schemas']['PortableEvidence'][]
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
  get_source_api_branches__branch_id__manuscript_sources__revision_id__get: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
        revision_id: string
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
          'application/json': components['schemas']['PortableRevision']
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
  list_work_snapshots_api_branches__branch_id__work_snapshots_get: {
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
  save_work_snapshot_api_branches__branch_id__work_snapshots_post: {
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
        'application/json': components['schemas']['SnapshotInput']
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
  preview_work_snapshot_api_branches__branch_id__work_snapshots__snapshot_id__preview_get: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
        snapshot_id: string
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
  restore_work_snapshot_api_branches__branch_id__work_snapshots__snapshot_id__restore_post: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
        snapshot_id: string
      }
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': components['schemas']['ainovel__work_snapshot__RestoreInput']
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
  diff_work_snapshot_chapter_api_branches__branch_id__work_snapshots__snapshot_id__chapters__chapter_id__get: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
        snapshot_id: string
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
  sync_status_api_branches__branch_id__desktop_sync_get: {
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
  enable_sync_api_branches__branch_id__desktop_sync_enable_post: {
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
        'application/json': {
          [key: string]: unknown
        }
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
  disable_sync_api_branches__branch_id__desktop_sync_disable_post: {
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
  run_sync_api_branches__branch_id__desktop_sync_run_post: {
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
        'application/json': {
          [key: string]: unknown
        }
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
  compare_sync_api_branches__branch_id__desktop_sync_conflicts__conflict_id__compare_post: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
        conflict_id: string
      }
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': {
          [key: string]: unknown
        }
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
  resolve_sync_api_branches__branch_id__desktop_sync_conflicts__conflict_id__resolve_post: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
        conflict_id: string
      }
      cookie?: never
    }
    requestBody: {
      content: {
        'application/json': {
          [key: string]: unknown
        }
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
        'application/json': components['schemas']['ainovel__models__RestoreInput']
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
        at_event_id?: string | null
        base_story_revision?: number | null
        chapter_revision?: number | null
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
  outline_workspace_api_branches__branch_id__outline_workspace_get: {
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
  save_workspace_api_branches__branch_id__outline_workspace_put: {
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
        'application/json': components['schemas']['SaveOutline']
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
  confirm_workspace_api_branches__branch_id__outline_workspace_confirm_post: {
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
        'application/json': components['schemas']['ConfirmOutline']
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
  outline_revision_api_branches__branch_id__outline_workspace_revisions__revision__get: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
        revision: number
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
  setting_options_api_branches__branch_id__outline_workspace_setting_options_get: {
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
  reference_options_api_branches__branch_id__outline_workspace_reference_options_get: {
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
          'application/json': components['schemas']['OutlineCard'][]
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
  adopt_api_branches__branch_id__ideas_adopt_post: {
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
        'application/json': components['schemas']['IdeaAdoptInput']
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
  read_preview_api_branches__branch_id__chapters__chapter_id__scene_times__identifier__reconfirmation_preview_get: {
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
  author_reconfirm_api_branches__branch_id__chapters__chapter_id__scene_times__identifier__reconfirm_post: {
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
        'application/json': components['schemas']['ReconfirmationInput']
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
  read_receipt_api_branches__branch_id__chapters__chapter_id__scene_times__identifier__reconfirmations__key__get: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
        chapter_id: string
        identifier: string
        key: string
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
  get_project_api_branches__branch_id__conception_project_brief_get: {
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
          'application/json': components['schemas']['ConfirmedProject'] | null
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
  confirm_project_api_branches__branch_id__conception_project_brief_put: {
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
        'application/json': components['schemas']['ConfirmProject']
      }
    }
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['ConfirmedProject']
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
  get_workspace_api_branches__branch_id__conception_get: {
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
          'application/json': components['schemas']['ConceptionWorkspace']
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
  save_workspace_api_branches__branch_id__conception_put: {
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
        'application/json': components['schemas']['SaveConception']
      }
    }
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['ConceptionWorkspace']
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
  adopt_candidate_api_branches__branch_id__conception_adopt_post: {
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
        'application/json': components['schemas']['AdoptConception']
      }
    }
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['ConceptionWorkspace']
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
  confirm_workspace_api_branches__branch_id__conception_confirm_post: {
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
        'application/json': components['schemas']['ConfirmConception']
      }
    }
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['ConceptionWorkspace']
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
  generate_conception_api_branches__branch_id__conception_generate_post: {
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
        'application/json': components['schemas']['ConceptionGenerateInput']
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
  generate_project_api_branches__branch_id__conception_project_brief_generate_post: {
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
        'application/json': components['schemas']['ProjectGenerateInput']
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
  extract_conception_world_api_branches__branch_id__conception_extract_world_post: {
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
        'application/json': components['schemas']['ConceptionWorldExtractInput']
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
  continue_conditional_policy_api_tasks__task_id__continue_conditional_policy_post: {
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
        'application/json': components['schemas']['ConditionalContinuationInput']
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
  reprepare_plan_preview_api_tasks__task_id__reprepare_plan_preview_get: {
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
  plan_restart_receipt_api_tasks__task_id__plan_restarts__client_request_id__get: {
    parameters: {
      query?: never
      header?: never
      path: {
        task_id: string
        client_request_id: string
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
  reprepare_plan_api_tasks__task_id__reprepare_plan_post: {
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
        'application/json': components['schemas']['PlanRestartInput']
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
  correction_preview_api_artifacts__artifact_id__source_interpretation_correction_preview_get: {
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
  create_correction_api_artifacts__artifact_id__source_interpretation_corrections_post: {
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
        'application/json': components['schemas']['CorrectionInput']
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
  correction_request_api_artifacts__artifact_id__source_interpretation_corrections__client_request_id__get: {
    parameters: {
      query?: never
      header?: never
      path: {
        artifact_id: string
        client_request_id: string
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
  recheck_preview_api_artifacts__artifact_id__recheck_source_correction_preview_get: {
    parameters: {
      query: {
        receipt_id: string
      }
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
  create_recheck_api_artifacts__artifact_id__recheck_source_correction_post: {
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
        'application/json': components['schemas']['RecheckCorrectionInput']
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
  recheck_request_api_artifacts__artifact_id__recheck_source_correction__client_request_id__get: {
    parameters: {
      query?: never
      header?: never
      path: {
        artifact_id: string
        client_request_id: string
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
  candidate_history_api_artifacts__artifact_id__history_get: {
    parameters: {
      query?: {
        offset?: number
      }
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
  candidate_acceptance_receipt_api_artifacts__artifact_id__acceptance_get: {
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
  recheck_candidate_api_artifacts__artifact_id__recheck_post: {
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
        'application/json': components['schemas']['CandidateRecheckInput']
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
  edit_saved_reference_api_reference_sessions__identifier__patch: {
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
  get_card_api_reference_sessions__identifier__cards__card_id__revisions__revision__get: {
    parameters: {
      query?: never
      header?: never
      path: {
        identifier: string
        card_id: string
        revision: number
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
          'application/json': components['schemas']['SavedReferenceCard']
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
  history_api_branches__branch_id__outline_workspace_generation_get: {
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
          'application/json': components['schemas']['OutlineGenerationTask'][]
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
  start_api_branches__branch_id__outline_workspace_generation_post: {
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
        'application/json': components['schemas']['GenerateOutline']
      }
    }
    responses: {
      /** @description Successful Response */
      202: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['OutlineGenerationTask']
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
  proposal_api_branches__branch_id__outline_workspace_generation__identifier__get: {
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
          'application/json': components['schemas']['OutlineProposal']
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
  start_api_branches__branch_id__workflow_inspections_semantic_post: {
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
        'application/json': components['schemas']['GenerateInspectionCandidate']
      }
    }
    responses: {
      /** @description Successful Response */
      202: {
        headers: {
          [name: string]: unknown
        }
        content: {
          'application/json': components['schemas']['InspectionCandidateTask']
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
  history_api_branches__branch_id__workflow_inspections_semantic_tasks_get: {
    parameters: {
      query?: {
        scope?: 'chapter' | 'volume' | 'book'
        scope_id?: string | null
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
          'application/json': components['schemas']['InspectionCandidateTask'][]
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
  candidate_api_branches__branch_id__workflow_inspections_semantic__identifier__get: {
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
          'application/json': components['schemas']['InspectionCandidate']
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
  latest_preparation_api_branches__branch_id__book_completion_preparation_get: {
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
  prepare_api_branches__branch_id__book_completion_preparation_post: {
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
        'application/json': components['schemas']['PrepareCompletion']
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
  runs_api_branches__branch_id__seven_stage_runs_get: {
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
  create_run_api_branches__branch_id__seven_stage_runs_post: {
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
        'application/json': components['schemas']['StartRun']
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
  pause_run_api_branches__branch_id__seven_stage_runs__task_id__pause_post: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
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
  resume_run_api_branches__branch_id__seven_stage_runs__task_id__resume_post: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
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
  cancel_run_api_branches__branch_id__seven_stage_runs__task_id__cancel_post: {
    parameters: {
      query?: never
      header?: never
      path: {
        branch_id: string
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
}
