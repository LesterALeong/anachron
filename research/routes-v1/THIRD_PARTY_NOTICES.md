# Third-party notices for Routes v1

Routes v1 will query the official English Wikipedia MediaWiki API and may write
local discovery artifacts containing revision metadata, revision text, and
derived diffs. Wikipedia content is available under the Creative Commons
Attribution-ShareAlike 4.0 International license. Preserve the revision `oldid`
URLs in each artifact so reviewers can attribute and inspect the exact source.
See <https://creativecommons.org/licenses/by-sa/4.0/> and the Wikimedia Terms of
Use at <https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use>.

Those full-text artifacts are ignored local review inputs and are never embedded
in a sealed runnable manifest. Sealing verifies them before retaining only the
immutable revision metadata, bounded evidence snippets, attribution, and an
artifact SHA-256 commitment.

The sampling frame follows the pinned ExAnte repository and dataset revisions
in `contract.json`. ExAnte's Hugging Face dataset card declares an MIT license.
No ExAnte data is vendored by this repository in this change. The later
sampling-frame builder fetches only the raw pinned `wiki/README.md` and
`exante_wiki.csv` artifact URLs in `contract.json`; its generated frame retains
both exact URLs, raw-source SHA-256 values, frozen upstream revisions, and the
observed source row and unique-pair counts before frozen-pair validation. The
official Hugging Face cache redirect is retained as a resolved URL and ETag so
the exact delivered object remains auditable.
