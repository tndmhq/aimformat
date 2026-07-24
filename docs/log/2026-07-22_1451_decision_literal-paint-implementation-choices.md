---
date: 2026-07-22 14:51
type: decision
status: superseded
related:
  - 2026-07-22_0844_plan_literal-element-paint.md
---

# Decision: literal paint implementation choices

The plan's nine design decisions are the maintainer's and were followed as
written. Implementing them still needed choices the plan did not settle.
Each is recorded here with its reasoning so it can be reviewed and reversed
cheaply, rather than discovered later as unexplained behaviour.

## 1. "Authored" means the author asked for a COLOUR, not for a border

Base-layer colours stay alone when nothing was declared (plan §4.6), and the
class cascade must include shorthand resets (§4.3). Those two rules meet in
`class="border-t border-red-600"`, where the winning `border-top-color` is
the grey inside `.border-t{border-top:1px solid #e5e7eb}` — a class
declaration, so "authored" by origin, but not a colour anyone chose.

Rule implemented: a computed value crosses into DOCX only when the winning
declaration's **property is a colour property** (`color`,
`background-color`, `border-color`, `border-<side>-color`) **and** its origin
is a class or inline style. A colour arriving as a component of a border
shorthand is the utility's default ink, exactly like the element base layer's
own defaults, and is not exported.

Why this is the right cut: `class="border"` alone still produces no Word
border (unchanged behaviour, and the plan's "an unpainted document gains no
explicit Word paint" invariant), while `class="border"
style="border-color:#ff69b4"` produces a pink one.

## 2. `hr` keeps its em-dash rule and paints the dashes

`hr` carries `border-top` from the base layer, so `border-color` recolours it
in HTML and PDF and its DOCX emitter is in scope (plan §Rendering). Word's
idiomatic horizontal rule is an EMPTY paragraph with a border — but an empty
paragraph with no border is invisible, so an unpainted `hr` would vanish, and
the no-explicit-paint invariant forbids giving it one unconditionally.
Emitting both a border and the dashes would draw two rules.

So the em-dash paragraph stays and an authored border colour paints the
dashes: an honest approximation of "the rule is pink", documented rather than
silent. Reversible — give `hr` a bordered empty paragraph when paint is
present and keep the dashes otherwise — at the cost of two shapes for one
element.

## 3. The refusal guard is "the history already verifies"

The plan requires that a document whose history cannot take the upgrade event
refuses paint. Implemented as: before recording the upgrade, run `verify()`;
if it reports anything, refuse. A damaged chain cannot account for the
document as it stands, so another state change on top of it would produce a
history that can never replay.

Cost, bounded deliberately: the check runs only when the document declares an
older version AND the payload actually contains paint — once in a document's
life. Validation clones pay it too while the first paint edit is projected. A
pruned-but-valid history passes (prune keeps a valid suffix), which is why
"pruned" is not by itself a refusal.

## 4. The upgrade event takes its own batch

`aim:version` events are appended before the edit that needs them and take
their own batch id unless the caller is already inside `doc.batch()`.
Grouping them would have meant restructuring six write paths, and undo is
per-event rather than per-batch anyway, so grouping buys nothing
behavioural. It does mean undoing a recolour twice returns the document to
0.2 — correct, just two steps.

## 5. `aim:version` is a reserved target, not an addressable one

`DocState.exists`/`serial`/`kind_of` deliberately do NOT know it. That makes
`propose_modify("aim:version", …)` and `modify_chunk("aim:version", …)`
raise, and a hand-authored card targeting it lint P008 — all without a new
rule. `_invert_on` and `_apply_data` special-case it directly, the way they
already special-case `aim:theme` and `aim:doc`.

## 6. The version comparison lives on `Registry`

`REGISTRY.implements(declared)` — "same or older is understood, newer is
not" — because both the linter (S002/S006) and the write path need it, and an
unparseable version counts as not understood. Versions compare as dotted
integer tuples.

## 7. The unreleased 0.2.1 CHANGELOG section became 0.3.0

0.2.1 was never published (0.2.0 is the released line) and the paint work
moves the spec version, so the whole unreleased set ships as 0.3.0 rather
than leaving a phantom 0.2.1 heading above it.

## 8. Spec §3.3's new examples use ` ```html ` fences

Every ` ```aim ` snippet in the spec is linted as a COMPLETE document by
`tests/test_spec.py`. The paint examples are fragments and would each need a
full document shell, burying the point of the section. The exact conformance
claims they illustrate are pinned in `tests/test_lint.py` (`TestInlinePaint`)
instead, which is where they belong.

## 9. A grouping background propagates, and stops at any declared background

CSS backgrounds do not inherit; the ancestor box is simply behind the
descendant. Word has no box, so the resolver propagates a "box showing
through" value downward for the DOCX approximation and stops it at any
element declaring its own background — including a base-layer one such as
`code{background:#f3f4f6}`, which is opaque in a browser too. Only authored
values are ever emitted.

## 10. Two pre-existing tests changed meaning, not just expectations

`S002`/`S006` moving to "warn only on newer" made three tests assert the old
contract. They were rewritten to pin the NEW contract in both directions —
older is silent, newer warns — rather than deleted, so the direction that
still warns keeps its coverage.
