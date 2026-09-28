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
