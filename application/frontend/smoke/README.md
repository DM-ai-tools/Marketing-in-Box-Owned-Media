# Render checks

`npm run smoke`

This project has no test runner. These two files mount the transcript's presentation layer
with `react-dom/server` and assert what came out, which is the cheapest way to prove the
cards still render every state a generation can be in — streaming, finished, saved,
superseded, interrupted, failed, refining — without a browser or a new dependency.

- `smoke.tsx` — the new presentation components in isolation (outline, section bodies,
  summary chips, live progress, overflow menu), plus the document parser's behaviour on
  documents shaped like real stage output.
- `card.tsx` — `GenerationStream` against a seeded store, and the reader pane.
- `shell.tsx` — the view bar, the sample transcript, the deliverables grid and the nav pills,
  plus the assertions that the sample fixtures really do drive the live extractors (the palette
  swatches must come from the document's prose, never from a fenced stylesheet).
- `clear.tsx` — "Clear chat" and the delete warning. Mostly about scope: clearing Phase 2 must
  leave Phase 1's cards, its parked slot and its run exactly where they are, and the button has to
  name the leg it would clear rather than claiming the chat.
- `competitors.tsx` — the competitor review card's choice of which competitors a stage uses (all,
  a ticked subset, or the operator's own list), the parser for a pasted list, and the listing each
  choice actually saves.
- `topics.tsx` — the "Topic suggestions" section at the top of an exported `.md`: which headline
  gate a document is paired with, what each topic says (why, funnel stage, content type, keyword
  basis), and that it is never put in front of an `.html` export.
- `visual.tsx` — the reader's Visual view. Over **every real output in `manual_execution/`** it
  checks two things:
  - a section's visual blocks rejoin to its body byte for byte;
  - every recognised block renders every word of its source.

  Then it checks the structure is actually recognised on the real samples (the CRO gauge, ladder
  offers, the SMS sequence, the lead-magnet winner, the funnel flow, kanban and heat table, and the
  webinar run of show), and gives each table rule a positive and a negative case. Finally, the reader
  opens in Visual, Text is the old markup, and grid cards carry the glance.
- `deleteCard.tsx` — "Delete this card" on a generation card: a draft goes (the last open draft
  leaves a "draft deleted" card with Start again), a refined draft takes its note with it and
  reopens the draft it came from, the stage's only approved card is refused, and the modal.
- `exports.tsx` — the plan map and the slide decks on the generation card and in the reader (one
  shared component, shown only when the file can be built), one row per deck plus "all", the
  `?index=` download, the replica events reaching the card with a download only once an assembly
  exists, a `replica_error` that leaves the stage complete, and image-failure notes.
- `plainOutline.tsx` — `lib/plainOutline.ts` on the real plain-text Phase 2 ICP in `manual_execution/`:
  it splits into its sixteen sections with every word kept, and every Markdown output in the
  corpus comes back byte-identical.
- `check.tsx` — the business check on a draft, driven through the store's real actions with the
  generation, check and refine routes stubbed. Covers:
  - a clean finish is checked with no click, and the panel shows scores, the "prediction" label on
    virality and the findings;
  - Approve stays on offer;
  - "Fix with Refine" sends the chosen findings, quoted;
  - a failed check offers a retry without failing the draft;
  - a failed draft is not checked.
- `phase2icp.tsx` — Phase 2's own ICP. Checks that a Phase 2 leg starts without Phase 1's ICP and
  value ladder, and that the ICP asks the audience's industry fresh even when Phase 1 answered it.
  The answer is filed against that run only, and later Phase 2 stages reuse it rather than Phase 1's.
- `industry.tsx` — the client's industry and the stage advisories, driven through the store's real
  actions with `fetch` stubbed. It covers the pause at the end of ICP intake, the confirm card
  versus the text box, the source each answer is filed with, and reuse of an industry already on the
  run. For advisories, it checks that the walk stops in front of a stage the industry rarely needs,
  that Skip passes it over without marking it saved, and that the default bucket changes nothing.
- `plan.tsx` — the Plan of Action tree, its layout, the diagram and the standalone HTML export.
  The losslessness check there is word-multiset containment rather than line matching, and the
  comment above it records why: two weaker versions of that check each let a real data-loss bug
  through.
- `ahrefsUsage.tsx` — the "Ahrefs keyword data" section at the end of an asset. It checks which
  assets get it and what it says and tables. It checks that the stub provider, a failed lookup or
  another leg's data never appear, and that the section comes last and is parsed as its own section.
- `secondaryKeywords.tsx` — the Pillar Page's secondary terms. Answering the head term opens the
  Ahrefs-vs-DataForSEO card, not a question, with nothing ticked. The two columns show their totals
  and the terms both suggested. A failed provider says why in its own column. The answer carries
  each term's provider and volume, and Skip answers N/A.
- `manus.tsx` — the Pillar Page built by Manus, on the card: the building state (the agent's own
  words, elapsed time, task link), the one-line done state with credits, the fallback note saying
  Claude built the page instead, and that a reloaded card never claims to still be building.
- `planPhase1.tsx` — the Plan of Action as Phase 1's last stage. It checks that the sub-service field
  opens the discovery card rather than a question, and that the confirmed answer and the
  `plan_sub_services` text filed on the run have the same line shape. It also checks that a skip
  tells the plan to infer them, and that a Phase 2 run built on that run lists the planned
  sub-services first.

## What they do not cover

Server rendering never runs effects, so these prove the **first paint** of each state, not
click handlers or the heading scan that drives the live step tracker (which runs in an
effect). Those are asserted structurally instead: the control exists, is enabled or disabled
correctly, and carries the right label.

See the comment above `render()` in `card.tsx` for why the store is seeded through
`getInitialState()` rather than `setState` alone.
