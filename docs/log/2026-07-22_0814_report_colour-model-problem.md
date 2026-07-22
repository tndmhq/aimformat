---
date: 2026-07-22 08:14
type: report
status: superseded
related:
  - 2026-07-22_0844_plan_literal-element-paint.md
---

# Report: how colour works in v0.1, and why it does not

> **Superseded 2026-07-22** by
> [`2026-07-22_0844_plan_literal-element-paint.md`](2026-07-22_0844_plan_literal-element-paint.md),
> which shipped as spec v0.3. §2 (the design problem), §4a and §4c (the DOCX
> inheritance and mixed-`pre` gaps) are all closed. Kept for the reasoning
> that led there.

A brief for redesigning colour. Written to be read on its own — everything
needed to reason about the problem is below, including the parts that work and
should not be thrown away.

The trigger was a one-line user report: *"adding a title in pink adds two
paragraphs: the title and the css… the title is not in pink when downloaded in
docx."* Fixing it exposed that **coloured text is not really a supported
capability**, and the gap is in the format, not in any one implementation.

## 1. What v0.1 actually offers

Colour can only be expressed through a **closed vocabulary of classes**. There
are exactly 21 distinct text-colour classes in the whole format:

| Source | Classes | Notes |
|---|---|---|
| Theme slots | `text-brand-1..4` | resolve to `--aim-brand-N`, set per document |
| Fixed palette | `text-{gray,red,green,amber}-{shade}` | gray has 10 shades; **red, green and amber have only `50` and `600`** |
| Singles | `text-white` | literal `#fff` |

Same three prefixes for `bg-` and `border-` (`classes.color_utilities`).

Two further constraints matter:

- **Inline colour is illegal.** `style_props.order` is
  `['left','top','width','height','transform','z-index']` — geometry only. A
  `style="color:#ff69b4"` fails lint with **V007**. This is deliberate (slides
  carry geometry in `style`), but it means there is *no* escape hatch for an
  arbitrary colour on an element.
- **Theme slot values are constrained but liberal**: the pattern is
  `^(#[0-9a-f]{3}|#[0-9a-f]{6}|rgb\(\d{1,3}, ?\d{1,3}, ?\d{1,3}\))$`. So a slot
  can hold *any* colour; a class cannot.

**Net effect: the only way to put an arbitrary colour on an element is to
repurpose one of the four brand slots.**

## 2. Why that is the wrong shape for "make this title pink"

### 2a. Brand slots are document-global

`--aim-brand-1` is consumed by more than the class that names it:

```
a                => color:var(--aim-brand-1)      ← every link
.text-brand-1    => color:var(--aim-brand-1)
.bg-brand-1      => background-color:var(--aim-brand-1)
.border-brand-1  => border-color:var(--aim-brand-1)
aim-proposal     => border-left:3px solid var(--aim-brand-1)   ← review chrome
```

So "make this heading pink" by setting `brand-1` **also repaints every link in
the document**. There is no per-element colour, only a shared palette; the
request is inherently local while the mechanism is inherently global.

### 2b. The agent cannot see enough to choose a slot safely

The edit prompt receives a **capped, relevance-selected set of chunks**, not the
document. So it cannot know which of the four slots are already in use. Any
instruction of the form "pick an unused slot" is advice the model is unable to
follow — it may repaint chunks it was never shown.

The current mitigation is honesty, not correctness: the model is told slots are
shared, told it cannot see the whole document, and told to **state the side
effect in its explanation** — *"set brand 2 to a pink hue; this recolours any
element using brand 2 across the document."* That is the right behaviour given the format, but it is a warning
label on a design problem.

### 2c. Four slots is a hard ceiling

A document wanting five distinct custom colours cannot express it at all.

### 2d. Cascade order is by sorted class name

`generate_aim_css()` emits class rules `sorted()` by name, and CSS is last-wins.
So for `class="text-brand-1 text-red-600"` **red wins regardless of the order
written in the markup**, because `text-red-600` sorts after `text-brand-1`.
Well-defined, but surprising, and every consumer (browser, PDF, DOCX) must
implement the same rule to agree. A sharp edge a redesign could remove.

## 3. What shipped, and what it cost

What the format's shape forced, and what each fix cost.

**Consumer side** (an editor built on this format — only the format-relevant
shape is recorded here)

Four separate defects, none of them format bugs, all downstream of the same
gap — the only way to express a colour is a shared token, so every layer had
to special-case theme edits:

- a theme proposal is a `<style>` payload, and any preview that reduces a
  payload to its text renders the stylesheet source at the reader;
- a colour class does nothing unless that surface also injects the document
  stylesheet and the theme variables, which is easy to do for one surface and
  forget for another;
- a colour-only change leaves the text identical, so any preview that strips
  `class` shows the reviewer no change at all — in the one place they decide;
- and an AI told not to emit class attributes has **only** the theme slot
  left. It set the slot, said the heading would be pink, and the heading
  rendered in default ink. It could not have done better with what the format
  offered.

**Format (`aimformat`)**

- `export_docx` emitted **no `w:color` at all** — every text colour was lost on
  DOCX export. Now resolved from `REGISTRY.class_declarations`, so any
  registered utility that sets `color:` is covered (including `text-white`,
  which lives in `classes.singles` and an earlier family-enumeration missed).
  Brand utilities resolve through the document theme; `rgb()` components are
  clamped to 255 as browsers do.
- Cascade order is honoured: the last-sorted colour class wins, matching
  `generate_aim_css()`.

**The cost is the signal.** Roughly ten review rounds on the editor side and
five on the format side. The later rounds were mostly finding defects in the
*previous* round's fix rather than in the original code — a reliable sign that
the shape is wrong, not that the work was sloppy.

## 4. What is still broken

### 4a. DOCX colour does not inherit (deliberately unimplemented)

CSS inherits colour, so `<div class="text-red-600"><p>Child</p></div>` renders
red in a browser and in the PDF. **DOCX exports it in default ink.**

This was implemented and then removed on purpose. The exporter has many leaf
emitters — block paragraphs, list items, `pre`, table cells, figure captions,
and each tracked-change path — and inherited colour must reach every one. Three
review rounds each found a *different* leaf still unthreaded, and one iteration
wrote inherited classes back onto source elements, corrupting the in-memory
document during a `pending="tracked"` export.

The durable fix is to resolve every element's effective colour **once** against
the whole tree and look it up by identity, rather than threading a parameter
down ~40 call sites. That is a real refactor and wants a human in the loop. A
test in `tests/test_ingest_export.py` documents the limitation so it is not
mistaken for an oversight.

**Colour applied directly to an element works everywhere, with one documented
exception below. Inheritance is missing, and only in DOCX.**

### 4c. One direct-colour hole in DOCX: mixed `<pre>`

`emit_pre` flattens a `<pre>` to a single run, so when the block holds BOTH
loose text and a coloured `<code>` child, adopting the child's colour would
paint the sibling text too. It therefore colours nothing:

```aim
<pre>plain <code class="text-red-600">x</code></pre>   → no w:color at all
<pre><code class="text-red-600">x</code></pre>         → red, as expected
```

Pinned by `TestDocxTextColour::test_a_pres_sibling_text_is_not_painted_by_a_nested_code`
— colouring nothing was chosen over colouring the wrong text (Codex #19). It
is a real direct-colour gap, not only an inheritance one, and it closes the
same way §4a does: resolve each element's effective paint once against the
tree, so a run can carry its own colour without the block guessing.
(Caught by Codex on #20 — the earlier wording sent the follow-up work at
inheritance alone.)

### 4b. The underlying design problem is untouched

Everything in §2 still holds. Nothing shipped changes the fact that:

- an arbitrary colour requires burning one of four shared, document-global slots;
- the agent cannot know which slots are safe to take;
- the format has no way to say "this element is `#ff69b4`" and mean only that.

## 5. Constraints any redesign has to respect

Not requirements — the things that will bite a proposal that ignores them.

1. **The closed vocabulary is load-bearing.** `registry.json` is what the
   verifier validates against, and the generated stylesheet is deterministic and
   size-tracked (`css_stats()`). "Just allow arbitrary inline colour" reopens
   the sanitising problem the whitelist exists to close, and V007 rejects it
   today.
2. **`style` is reserved for slide geometry.** Slide children carry their whole
   position there, and the editor re-attaches it when the model omits it.
   Anything putting colour into `style` collides with that.
3. **Every exporter must agree.** Browser, PDF and DOCX render the same
   document; a rule only one can implement produces exports that disagree with
   the canvas — which already happened once (editor showed brand, export showed
   grey).
4. **The agent sees a partial document.** Any mechanism requiring global
   knowledge ("pick a free slot", "check nothing else uses this") cannot be
   driven reliably by the editor's AI.
5. **Proposals must be reviewable.** Colour changes arrive as pending proposals
   a human accepts; the review card has to be able to *show* the resulting
   colour, including when the colour and the element arrive as two proposals in
   one turn.
6. **Round-trip stability, precisely.** `loads(dumps(x)) == x`, and colour
   must survive **export** to DOCX/PDF/HTML and a reload of the `.aim`.
   Preserving colour on the way IN is deliberately NOT a constraint here: the
   ingestors drop presentation generally, and the cause is upstream — docling's
   formatting model carries only bold/italic/underline/strikethrough, so
   colour, alignment and font size never reach the mapping code at all
   (measured 2026-07-22, recorded in
   [`docs/knowledge/architecture.md`](../knowledge/architecture.md)).
   Recovering them needs a python-docx side pass over the original file and is
   its own project. Stating it as a constraint here conflicted with the
   implementation plan, which excludes it — leaving two active log entries with
   contradictory acceptance criteria (Codex on #20).

## 6. Directions worth arguing about

Deliberately not a recommendation — this is the brief, not the decision.

- **Per-element colour token.** A registered way for an element to carry its own
  colour (a constrained attribute, or extending the class vocabulary to accept a
  literal) without touching a shared slot. Retires §2a, §2b and §2c at once.
  Cost: widens the closed vocabulary; every exporter and the sanitiser must
  handle it.
- **More slots, or named slots.** Cheap, and solves neither §2a nor §2b — a
  bigger shared palette is still shared.
- **Scoped theme overrides.** A theme block scoped to a subtree, so "pink here"
  does not mean "pink everywhere". Fits CSS's model; adds cascade complexity
  that DOCX in particular will struggle with (see §4a).
- **Leave colour global, make the UI honest.** Accept colour as a document-level
  concern and treat "make this title pink" as a theme edit the user reviews as
  such. Cheapest; answers a slightly different question than the one asked.

Whatever is chosen, §4a gets much easier if colour stops needing to inherit
through arbitrary nesting — a per-element token would make the DOCX exporter's
job local again.
