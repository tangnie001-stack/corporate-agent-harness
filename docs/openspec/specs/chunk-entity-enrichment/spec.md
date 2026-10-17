# chunk-entity-enrichment Specification

## Purpose
TBD - created by archiving change chunk-entity-metadata-enrichment. Update Purpose after archive.
## Requirements
### Requirement: Document-level entity extraction

The system SHALL extract business entities from a document at ingestion time, once per document. **Core entities** (`company`, `report_period`, `sec_code`) SHALL be rendered into prompt context; **optional entities** (`person`, `currency`, `report_type`) SHALL be kept as supplementary metadata without rendering priority.

Extraction SHALL use a three-layer pipeline:
1. Filename regex (e.g., `neusoft_2025_q1.pdf` → company/year/quarter)
2. Heading-stack rules (reusing ContextStack-style parent inheritance from heading tree)
3. LLM validation fallback (gated by three-state `ENTITY_LLM_FALLBACK`: `off` / `on` / `auto`), which corrects rule mis-matches and fills gaps (e.g., person names, non-rule-covered companies). In `auto` mode, the LLM runs only when the rule layer produces an empty result or misses a core entity.

The LLM fallback SHALL receive: filename + full heading tree + first 500~800 chars of document body, and SHALL output `{rule_correct, reason, entities}`.

Extraction failure SHALL NOT block ingestion; the system SHALL fall back to rule-layer results or an empty entity dict.

#### Scenario: Neusoft quarterly report extraction
- **WHEN** a document named `neusoft_2025_q1.pdf` is ingested
- **THEN** extracted entities SHALL include `company=东软集团` (or full name), `report_period=2025年第一季度`, `sec_code=600718`

#### Scenario: Rule mis-match corrected by LLM
- **WHEN** the rule layer extracts `company=世界领先的互联网科技公司` from a Tencent annual report
- **THEN** the LLM fallback SHALL correct it to `腾讯控股有限公司`

#### Scenario: LLM fallback disabled
- **WHEN** `ENTITY_LLM_FALLBACK=off` and the rule layer produces no result
- **THEN** ingestion proceeds with an empty entity dict, and extraction is logged as incomplete

#### Scenario: LLM fallback auto-mode triggers on missing core entity
- **WHEN** `ENTITY_LLM_FALLBACK=auto` and the rule layer returns `company` but misses `report_period`
- **THEN** the LLM validation fallback SHALL run to fill the missing core entity

### Requirement: Entity injection into chunk metadata

The system SHALL inject extracted entities into every chunk's metadata of that document before storing it into the `chunks` table.

The system SHALL also aggregate entities into `document.meta_info` (`{"entities": {...}}`) in the relational store as the document-level authoritative store.

#### Scenario: All chunks carry document entities
- **WHEN** a document with 50 chunks is ingested
- **THEN** all 50 chunk metadata records SHALL contain the same `company` / `report_period` / `sec_code` fields

#### Scenario: Document meta_info aggregation
- **WHEN** a document finishes ingestion
- **THEN** `document.meta_info` SHALL contain an `entities` key with the document-level entity dict

### Requirement: Heading path binding via offset reverse-lookup

The system SHALL bind each chunk to its parent heading section by reverse-locating the chunk content within the full text (`full_text.find`) against a heading-segment interval table, writing only `chunk.metadata["heading_path"]` without modifying chunk content.

Existing chunker behaviors (parent_child / table_preserving / qa) and the table-preserving 4-layer protection SHALL remain unchanged.

#### Scenario: Chunk under financial data section
- **WHEN** a chunk's content originates from the "一、主要财务数据" section of a report
- **THEN** its `metadata["heading_path"]` SHALL point to that section heading

#### Scenario: Table chunk content unchanged
- **WHEN** a table chunk goes through heading-path binding
- **THEN** its `content` SHALL remain byte-identical (no heading prefix injected)

### Requirement: Entity rendering into prompt context

The system SHALL render entity metadata into the LLM prompt context via `RAGContext.to_prompt_text()`, rendering only entities that exist, in the order defined by `ENTITY_RENDER_ORDER`.

The rendered format SHALL be identical for production prompt generation and evaluation judge context.

#### Scenario: Entities appear in prompt text
- **WHEN** a context has `entities={company: 东软集团, report_period: 2025年第一季度}`
- **THEN** `to_prompt_text()` output SHALL include both fields in the order defined by `ENTITY_RENDER_ORDER`

#### Scenario: Empty entities render cleanly
- **WHEN** a context has no entities
- **THEN** `to_prompt_text()` output SHALL match the previous format (only source/page/content), without empty entity markers
