# Source adapter instructions

These instructions apply to `src/aranami/sources/` in addition to the
parent package and repository-wide instructions.

## Source boundaries

- Keep source adapters low-level and read-only.
- Keep connection, schema, and transport details inside the relevant source
  adapter. Higher layers consume typed results rather than connections,
  SQLAlchemy statements, or raw responses.
- A source adapter may remain one module or become a domain-named
  subpackage as its implementation grows.

## Wiki Replica database access

- Database access is strictly read-only and targets Wikimedia Wiki
  Replicas. Never use `insert()`, `update()`, `delete()`, DDL, ORM
  persistence, `Session.add()`, `Session.delete()`, or `commit()`.
- Prefer the Wiki Replica-backed Quarry adapter over Pywikibot API access
  whenever replicated tables contain the required read-only wiki metadata.
- Use SQLAlchemy Core expressions and `select()` for every query. Define or
  reflect tables and compose joins, filters, grouping, ordering, and limits
  through SQLAlchemy.
- Do not implement queries with raw SQL strings or `text()`. If a required
  read-only operation genuinely cannot be represented by SQLAlchemy, stop
  and ask for explicit direction before adding raw SQL.
- Close connections promptly. Apply sensible limits and use the compound
  indexes provided by the MediaWiki schema.
- Keep all Wiki Replica connection and schema details inside the Quarry
  source adapter.
- Refer to the [MediaWiki database layout][1] for table structure and the
  [SQLAlchemy ORM documentation][2] for SQLAlchemy usage.

## Pywikibot fallback reads

- Use high-level Pywikibot methods only when replicas cannot provide the
  required read-only information, such as current page text.
- Prefer Pywikibot's high-level objects and methods, including `Site`,
  `Page`, `Category`, and page generators.
- Do not use `pywikibot.api`, `pywikibot.data.api`, or construct raw API
  requests when a supported high-level method can perform the operation.
- If no high-level method exists, isolate the direct API call behind a
  narrowly named source adapter and document the missing Pywikibot
  capability.
- When retrieving content or status for multiple pages, wrap the page
  iterable with `pywikibot.pagegenerators.PreloadingGenerator` instead of
  triggering one fetch per page.
- Refer to the [Pywikibot documentation][3], including its
  [preloading guidance in the FAQ][4].

[1]: https://www.mediawiki.org/wiki/Manual:Database_layout
[2]: https://docs.sqlalchemy.org/en/20/orm/
[3]: https://doc.wikimedia.org/pywikibot/stable/
[4]: https://doc.wikimedia.org/pywikibot/stable/faq.html
