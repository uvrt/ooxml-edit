# What the siblings import

Back to the [README](../README.md).

The agents (pptx-agent, docx-agent, xlsx-agent) and ooxml-common's tests import these
names from ooxml-edit, in their `src`, `tests` and `tools`. They are its surface across
repositories: their CI installs ooxml-edit from `main`, so renaming or removing one breaks
a sibling at its next run, not on the pull request that made the change.
`tests/test_public_api.py` lists the same names, each with the siblings that use it, and
fails when one is gone -- or when this page and that list disagree.

To rename one, keep the old name as an alias for one release (the test says who it is kept
for), and move the sibling to the new name. A sibling that starts importing a name
adds it here and to the test.

Each line is a module, then which siblings import what from it.

- `ooxml_edit.charts` -- docx-agent, pptx-agent: `add_chart`; docx-agent: `ChartDataError`, `ChartDataWarning`, `WORD_LOOK`; pptx-agent: `POWERPOINT_LOOK`, `provenance`
- `ooxml_edit.charts.actions` -- docx-agent, pptx-agent: `STYLE_ACTIONS`, `add_style_actions`, `apply_add_style`, `apply_style_action`, `category_labels`, `chart_style_json`, `resolve_category`, `resolve_node`, `resolve_series`, `scale_axis`
- `ooxml_edit.charts.chart` -- docx-agent, pptx-agent: `Chart`, `ChartDataError`, `ChartDataWarning`, `chart_model`; pptx-agent: `AXIS_TAGS`, `C_NS`, `PLOT_TAGS`, `PLOT_TYPES`, `REL_PACKAGE`, `Series`, `decode_number`, `series_of`, `workbook_part`
- `ooxml_edit.charts.create` -- docx-agent: `WORD_LOOK`, `chart_data`, `check_options`; pptx-agent: `POWERPOINT_LOOK`
- `ooxml_edit.charts.diagram` -- docx-agent, pptx-agent: `Diagram`, `diagram_model`; docx-agent: `DiagramDrawingError`, `DiagramModel`; pptx-agent: `DATA_MODEL_EXT_URI`, `DGM_NS`, `DSP_NS`, `DiagramNode`, `REL_DIAGRAM_DRAWING`, `TEXT_TYPES`, `new_model_id`
- `ooxml_edit.charts.dmltext` -- docx-agent, pptx-agent: `replace_body_text`; pptx-agent: `LINE_BREAK`, `RUN_TAGS`, `inline_items`, `make_break`, `make_run`, `paragraph_like`, `paragraph_text`, `rebuild_paragraph`, `resegment`, `runs`
- `ooxml_edit.charts.format` -- pptx-agent: `scheme_color`, `split_color`
- `ooxml_edit.charts.host` -- docx-agent, pptx-agent: `GraphicHost`, `chart_part`; docx-agent: `diagram_parts`
- `ooxml_edit.charts.model` -- docx-agent, pptx-agent: `ChartModelError`, `apply_chart_model`, `apply_diagram_model`, `canonical_chart`, `canonical_diagram`; pptx-agent: `CHART_KEYS`, `CHART_READ_ONLY`, `chart_model`, `diagram_model`
- `ooxml_edit.charts.workbook` -- pptx-agent: `Area`, `Cell`, `MAX_COLUMN`, `MAX_ROW`, `SML_NS`, `SheetRange`, `Table`, `Workbook`, `Worksheet`, `column_index`, `column_letters`, `number_text`, `parse_formula`
- `ooxml_edit.dates` -- ooxml-common: `LANGUAGES`, `LCIDS`, `NAMES`, `format_code`, `format_date`, `is_date_code`, `serial`
- `ooxml_edit.errors` -- docx-agent, pptx-agent, xlsx-agent: `ArgumentError`, `UnitError`; pptx-agent: `ArgumentTypeError`
- `ooxml_edit.history` -- docx-agent, pptx-agent, xlsx-agent: `History`; docx-agent, pptx-agent: `Snapshottable`
- `ooxml_edit.markdown` -- xlsx-agent: `Line`, `Run`, `merge`, `parse`, `plain`, `to_markdown`
- `ooxml_edit.opc` -- docx-agent, pptx-agent, xlsx-agent: `OpcPackage`, `REL_OFFICE_DOCUMENT`, `rels_path_for`; docx-agent, pptx-agent: `CONTENT_TYPES_PART`, `Relationship`, `normalize_part_path`; pptx-agent: `RELS_NS`, `REL_IMAGE`, `numbered_template`, `part_for_rels`, `resolve_target`
- `ooxml_edit.provenance` -- docx-agent, pptx-agent: `RecordError`, `dumps`, `loads`; pptx-agent: `VERSION`, `facts_after_edit`, `facts_found`, `offset`, `place`, `record`
- `ooxml_edit.sheets` -- xlsx-agent: `Area`, `Cell`, `Formula`, `MAX_COLUMN`, `MAX_ROW`, `MAX_TEXT`, `RichText`, `column_index`, `column_letters`, `date_serial`, `number_text`, `outside_footprint`, `rich_runs`
- `ooxml_edit.stamp` -- pptx-agent: `ExtensionStamp`
- `ooxml_edit.tools` -- docx-agent, pptx-agent, xlsx-agent: `CORE`, `Dataset`, `DatasetEntry`, `DocumentFormat`, `ROLES`, `Result`, `ToolError`, `ToolGroup`, `Toolbox`, `array`, `boolean`, `integer`, `number`, `obj`, `strict_problems`, `string`, `tool`; docx-agent, pptx-agent: `InProcess`, `REVIEW`, `SubprocessPool`, `names_as`, `page_list`, `page_text`, `render`, `worker`, `worker_task`; docx-agent: `ACCEPT`, `content_key`; pptx-agent: `FolderCatalogue`, `Param`, `collect_warnings`; xlsx-agent: `CACHE_EXTERNAL`, `CACHE_STATUSES`, `DATASET_ID`, `DATASET_PREFIX`, `REF`
- `ooxml_edit.tools.adapters` -- docx-agent, pptx-agent, xlsx-agent: `anthropic_problems`, `openai_problems`; docx-agent, pptx-agent: `definitions_for`; xlsx-agent: `free_strings`
- `ooxml_edit.tools.assets` -- pptx-agent: `MAX_RESULTS`, `fetch`, `find`
- `ooxml_edit.tools.hints` -- docx-agent, pptx-agent, xlsx-agent: `tool_hints`
- `ooxml_edit.tools.limits` -- xlsx-agent: `LimitError`, `Limits`, `check_package`
- `ooxml_edit.tools.render` -- docx-agent, pptx-agent: `Layout`, `RendererBase`, `View`, `draw`, `draw_tiles`, `fingerprint`, `png_size`, `system_fonts`, `warm_up`; docx-agent: `lay_out_views`; pptx-agent: `raster_key`, `render_views`
- `ooxml_edit.tools.results` -- pptx-agent: `page_list`
- `ooxml_edit.tools.schema` -- docx-agent, pptx-agent, xlsx-agent: `example_arguments`, `is_path_name`, `walk_properties`; docx-agent, xlsx-agent: `subset_problems`; xlsx-agent: `close_match`
- `ooxml_edit.tools.shared` -- docx-agent, pptx-agent, xlsx-agent: `GROUPS`, `SESSION_TOOLS`, `handler`; docx-agent, pptx-agent: `CHART_TYPES`, `COMMENT_ACTION_ROLES`, `comment_role`; pptx-agent, xlsx-agent: `definition`; pptx-agent: `SPECS`
- `ooxml_edit.tools.sources` -- docx-agent, pptx-agent: `date_pattern`, `first_rows`, `no_dates`, `what`; pptx-agent: `runs`
- `ooxml_edit.tools.tiles` -- docx-agent, pptx-agent: `MAX_TILES`, `check_size`, `fit`, `narrowed_note`, `tiled_svg`, `tiling_of`
- `ooxml_edit.tools.worker` -- docx-agent, pptx-agent: `task_reference`
- `ooxml_edit.xml` -- docx-agent, pptx-agent, xlsx-agent: `Element`, `parse_xml`; docx-agent, pptx-agent: `CHILD_ORDER`, `NAMESPACES`, `append_in_order`, `child_elements`, `find`, `findall`, `get_int`, `insert_in_order`, `local_name`, `make`, `prefixed_name`, `qn`, `register_child_order`, `register_namespaces`, `remove`, `serialize`, `set_attr`, `subelement`; pptx-agent: `child_ranks`, `declared_prefixes`, `get_bool`, `iter_descendants`, `replace_choice`, `set_int`
