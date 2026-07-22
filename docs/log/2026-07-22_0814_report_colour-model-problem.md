---
date: 2026-07-22 08:14
type: report
status: active
related: []
---

# Report: how colour works in v0.1, and why it does not

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
effect in its explanation**. In production it now says things like *"Set brand 2
to a pink hue; this will recolour any element that uses brand 2 across the
document."* That is the right behaviour given the format, but it is a warning
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

All merged and in production.

**Editor (`tndm`)**

- Theme proposals rendered as literal CSS text in the document, because every
  preview path reduces a payload to `textContent` and for a `<style>` element
  that *is* the stylesheet source. They now render as named slots with a swatch.
- The flow editor had **no `.text-brand-*` rules and no `--aim-brand-*`
  variables at all** — the document stylesheet is injected only for slides. So
  even with the right class, a heading rendered in default ink.
- The proposal preview stripped every `class`, so a colour-only change (whose
  text is unchanged) showed an uncoloured heading in the one place a human
  reviews it.
- The prompt **forbade all class attributes**, so the model's only colour lever
  was a theme slot — and nothing makes a heading read `--aim-brand-*`. It set
  the slot, told the user the heading would be pink, and it rendered at
  `rgb(33,26,18)`. It could not have done better. Brand utilities are now an
  explicit exception, and `span` is allowed solely to carry one so "make only
  the word Confidential pink" has a legal wrapper.

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

**Colour applied directly to an element works everywhere. Only inheritance is
missing, and only in DOCX.**

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
6. **Round-trip stability.** `loads(dumps(x)) == x`, and colour must survive
   ingest from DOCX/PDF/Markdown and export back.

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
