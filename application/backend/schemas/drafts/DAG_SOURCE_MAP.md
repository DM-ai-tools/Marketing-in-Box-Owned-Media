# DAG Source Map — Field Schema Registry (v2, canonical `application/backend/assets/Prompts/` source)

This supersedes the old `manual_execution/`-sourced registry as the source of truth. All paths below
are relative to the repository root. All source prompt files themselves live under
`application/backend/assets/Prompts/`.

## Main assets (15 asset_ids, 16 schema files — `offers` has two versions)

| # | asset_id | version(s) | source_prompt_file | Paired competitor_analysis asset_id |
|---|---|---|---|---|
| 1 | `icp` | 1 | `application/backend/assets/Prompts/ICP.md` | — (root asset, no upstream) |
| 2 | `cro` | 1 | `application/backend/assets/Prompts/Master_Prompt_Universal_Page_Rewrite_v1.md` | `competitor_analysis_cro` |
| 3 | `pillar_page` | 2 | `application/backend/assets/Prompts/Master_Prompt_Universal_Page_Design_v1.md` (v2.0 of that prompt — single merged stage: supersedes the former separate `seo_pillar_page` "SEO-focused variant pass" over the same file, whose SEO + competitor-benchmark work is now Step 2, Rules 8–9, PART 2 and PART 4 of this one prompt) | `competitor_analysis_seo_pillar_page` |
| 4 | `funnel` | 1 | `application/backend/assets/Prompts/Funnel_Prompt.md` | none |
| 5 | `funnel_hub_media` | 1 | `application/backend/assets/Prompts/Funnel-Hub-Media-Architect-Prompt.md` (distinct asset from `funnel`, per explicit user decision — not a version of the same asset) | none |
| 6 | `offers` | 1 (legacy), 2 (current) | v1: `manual_execution/Value-Ladder-Genie-Prompt.md` (kept for reproducing older runs only, not re-sourced from the new canonical set); v2: `application/backend/assets/Prompts/Master_Prompt_Universal_Value_Ladder_v2.md` | `competitor_analysis_offers` |
| 7 | `lead_magnet` | 1 | `application/backend/assets/Prompts/Lead-Magnet-Architect-Prompt.md` | `competitor_analysis_lead_magnet` |
| 8 | `blog` | 1 | `application/backend/assets/Prompts/universal-blog-generation-prompt.md` | `competitor_analysis_blog` |
| 9 | `content_marketing_strategy` | 1 | `application/backend/assets/Prompts/Content-Marketing-Strategy-Architect-Prompt.md` | `competitor_analysis_content_marketing` |
| 10 | `social_content_strategy_audit` | 1 | `application/backend/assets/Prompts/Social-Content-Strategy-Audit-Architect-Prompt.md` | `competitor_analysis_social_content_strategy` |
| 11 | `webinar` | 1 | `application/backend/assets/Prompts/universal-webinar-prompt.md` (single merged stage — supersedes the old DAG's separate "competitor-sourced" / "LLM-sourced" webinar stages) | `competitor_analysis_webinars` |
| 12 | `book` | 1 | `application/backend/assets/Prompts/Webinar-to-Book-Architect-Prompt.md` | `competitor_analysis_book` |
| 13 | `podcast` | 1 | `application/backend/assets/Prompts/universal-podcast-prompt.md` | `competitor_analysis_podcast` |
| 14 | `sms_sequence` | 1 | `application/backend/assets/Prompts/universal-sms-sequence-prompt.md` | none |
| 15 | `plan_of_action` | 1 | `application/backend/assets/Prompts/Plan-of-Action-Architect-Prompt.md` | none — uses a general, plain competitor list, not one of the 10 numbered competitor-analysis files |

**Reference document, not a stage:** `application/backend/assets/Prompts/CRO_Framework_Universal_v1.md` is a
shared reference/framework library consumed by the `cro` asset's "CRO Framework" input (with a
`"USE DEFAULT"` fallback that reproduces this file's logic inline). It defines the 5-layer conversion
framework, terminology variables, the 13-section page architecture, pricing disclosure modes, and claim
substantiation tiers used across several other prompts. It has no `[YOUR ANSWER]`-style input block and
does not get its own asset_id / schema file.

**Count reconciliation:** 15 main asset_ids × their source files, minus the CRO Framework reference doc
(not a stage) = 15 stage schemas from the main prompt set (16 JSON files, since `offers` has v1 and v2).
Plus 11 competitor-analysis stages (`competitor_analysis_funnel` added for the funnel's Step 2) = **26 total stage schemas** in this directory.
Of the 15 main assets, **10 have a paired competitor_analysis_<asset> stage** (cro, pillar_page, offers,
lead_magnet, blog, content_marketing_strategy, social_content_strategy_audit, webinar, book, podcast);
5 do not (icp, funnel, funnel_hub_media, sms_sequence, plan_of_action).

Both counts were 16 main assets / 26 stages until the **Pillar Page merge**: `seo_pillar_page` was never
a distinct prompt — it re-ran `Master_Prompt_Universal_Page_Design_v1.md` a second time with a one-line
"make this the SEO variant" prelude, which meant the SEO pass had no access to the design system the
first pass extracted and the competitor pillar-page benchmark never reached the page that was actually
built. That file is now v2.0 and does both jobs in one run, so `seo_pillar_page.json` was deleted and
`competitor_analysis_seo_pillar_page` re-paired onto `pillar_page`. (Precedent: row 11, `webinar`, which
merged the old DAG's two webinar stages the same way.)

## Competitor Analysis stages (10 asset_ids)

All ten share an identical input-field shape (confirmed by reading every file individually, not just
`00_README.md`): `target_url` (required), plus four optional fields — `service`, `niche`, `location`,
`competitor_type` (enum: `niche_specialist` / `full_stack_niche`, both included if blank) — and
`excluded_competitors` (optional dedup list). No file among the 10 has extra INPUT fields beyond this
shared set; what differs per file is (a) the qualifying criteria written into each file's `service`
description (e.g. what counts as a genuine "lead magnet" vs. a "pillar page" vs. a "webinar"), and
(b) the *output* JSON schema (e.g. `starting_price_aud` for Offers, `topical_focus` for Podcast) — output
shape is not part of this input-field registry.

| # | asset_id | source_prompt_file | Paired main asset_id |
|---|---|---|---|
| 1 | `competitor_analysis_cro` | `application/backend/assets/Prompts/Competitor Analysis/01_CRO.md` | `cro` |
| 2 | `competitor_analysis_offers` | `application/backend/assets/Prompts/Competitor Analysis/02_Offers.md` | `offers` |
| 3 | `competitor_analysis_lead_magnet` | `application/backend/assets/Prompts/Competitor Analysis/03_Lead_Magnet.md` | `lead_magnet` |
| 4 | `competitor_analysis_blog` | `application/backend/assets/Prompts/Competitor Analysis/04_Blog.md` | `blog` |
| 5 | `competitor_analysis_seo_pillar_page` | `application/backend/assets/Prompts/Competitor Analysis/05_SEO_Pillar_Page.md` | `pillar_page` (was `seo_pillar_page` before the merge) |
| 6 | `competitor_analysis_content_marketing` | `application/backend/assets/Prompts/Competitor Analysis/06_Content_Marketing.md` | `content_marketing_strategy` |
| 7 | `competitor_analysis_social_content_strategy` | `application/backend/assets/Prompts/Competitor Analysis/07_Social_Content_Strategy_and_Posts.md` | `social_content_strategy_audit` |
| 8 | `competitor_analysis_webinars` | `application/backend/assets/Prompts/Competitor Analysis/08_Webinars.md` | `webinar` |
| 9 | `competitor_analysis_book` | `application/backend/assets/Prompts/Competitor Analysis/09_Book.md` | `book` |
| 10 | `competitor_analysis_podcast` | `application/backend/assets/Prompts/Competitor Analysis/10_Podcast.md` | `podcast` |

## Old draft files: overwritten vs. kept vs. superseded

| Old file | Disposition |
|---|---|
| `lead_magnet.json` | Overwritten in place — field content unchanged, but `source_prompt_file` repointed to the new canonical path and `competitor_lead_magnet_list` converted from a plain `file_attach` to a `context_reference` sourced from `competitor_analysis_lead_magnet` |
| `offers_v1.json` | Kept as-is — sourced from `manual_execution/Value-Ladder-Genie-Prompt.md`, explicitly retained per the original resolution log as the queryable historical version. Not re-sourced from the new Prompts folder. |
| `offers_v2.json` | Overwritten in place — confirmed field-for-field against the new canonical `Master_Prompt_Universal_Value_Ladder_v2.md` (no `regulated_field_flag` field exists in either the old draft or the new source — only `claim_substantiation_tier`, so no discrepancy there); `source_prompt_file` repointed and `competitor_analysis` field's `context_key` resolved from `unresolved_context_key` to `competitor_analysis_offers` |
| `plan_of_action.json` | Overwritten in place — field content unchanged (verbatim match against the new source), `source_prompt_file` repointed, confirmed no paired competitor-analysis file applies |
| `content_marketing_strategy.json` | Overwritten in place — field content unchanged, `source_prompt_file` repointed, `competitor_list` converted to `context_reference` sourced from `competitor_analysis_content_marketing`, and the previously self-referential `existing_content_assets_optional` context_key (`"content_marketing_strategy"`, which pointed the field at its own asset's output — a bug in the prior draft) corrected to `unresolved_context_key` since it can point at any of several upstream document types (Plan of Action, funnel document, lead magnet, ROI calculator, or ICP) |
| `social_content_strategy_audit.json` | Overwritten in place — field content unchanged, `source_prompt_file` repointed, `competitor_list` context_key resolved from `unresolved_context_key` to `competitor_analysis_social_content_strategy` |
| `funnel_hub_media.json` | Overwritten in place — field content unchanged (verbatim match against the new source), `source_prompt_file` repointed, and three new context_reference fields added (`icp_document`, `design_tokens`, `existing_funnel_document`) to make the prompt's previously-generic "reference folder" dependencies explicit |
| `book.json` | Overwritten in place — field content unchanged (verbatim match against the new source), `source_prompt_file` repointed, `source_transcript_recording` converted from `file_attach` to a `context_reference` sourced from `webinar_script`, and three new optional context_reference fields added (`content_marketing_strategy_optional`, `plan_of_action_summary_optional`, `competitor_analysis_book`) |
| `REVIEW_NEEDED.md` | Kept as historical record of the first migration pass (from `manual_execution/`). Not modified. This pass's new/unresolved items are in `REVIEW_NEEDED_V2.md`. |

All other files in this directory (`icp.json`, `cro.json`, `pillar_page.json`,
`funnel.json`, `blog.json`, `webinar.json`, `podcast.json`, `sms_sequence.json`, and all 10
`competitor_analysis_*.json` files) are newly authored in this pass — there was no prior draft for them.
