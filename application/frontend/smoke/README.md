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

## What they do not cover

Server rendering never runs effects, so these prove the **first paint** of each state, not
click handlers or the heading scan that drives the live step tracker (which runs in an
effect). Those are asserted structurally instead: the control exists, is enabled or disabled
correctly, and carries the right label.

See the comment above `render()` in `card.tsx` for why the store is seeded through
`getInitialState()` rather than `setState` alone.
