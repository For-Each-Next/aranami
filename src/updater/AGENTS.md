# Updater instructions

Updater projects prepared facts into wikitext and performs intentional
publication at explicit destinations. Runtime coordinates each report pass;
updater owns no scheduler or acquisition workflow.

- Consume typed data and caller-supplied settings. Do not fetch source pages or
  templates, analyze retained source formats, or manage datasets and caches.
- `services` contains output projections. `publisher` owns destination reads,
  precondition checks and high-level Pywikibot saves. Runtime jobs own the
  sequence from task preparation through retained proposal to confirmation.
- Preserve caller-provided baseline text and unambiguous paired managed ranges.
  Keep attributes, functional tags and text outside those ranges unchanged.
- Render the same complete facts retained for other consumers. Display limits
  must not discard stored observations or turn unknown values into zeroes.
- Use `edit_summary.py` for shared summary formatting. The first sentence may
  begin with totals; attach observed additions/removals using `summary_change`
  as `(+n)`, `(-n)` or `(+n/-n)`. Put article details in a separate sentence.
  Format counts and signed changes with `format_summary_number`, using U+202F
  narrow no-break spaces. Preserve date, title, URL and ID digits.
  English comparisons use totals such as
  `3 042 articles (+1): FA 301, FL 101, GA 2 640 (+1).`
  Use compact class groups such as `GA`, `FA (from GA)`, `DGA` and `FFA`.
  Put daily report dates after the
  action, such as `Change 21 assessments for Nov 2.` Include measured totals
  and useful compact changes. Reserve the
  exact suffix ` ([[toolforge:aranami|Aranami]])` and stay within 255 UTF-8 bytes.
  Shorten complete clauses/links, never characters or link syntax.
- `dry=True` never saves or requests delegated refreshes. Runtime/data retain
  the proposed edit and preview artifacts without advancing production state.
- Depend on data and shared contracts. Acquisition tasks, runtime lifecycle and
  scheduling must not be imported into output services.

See [Architecture](../../docs/architecture.md) and
[Projection instructions](services/AGENTS.md).
