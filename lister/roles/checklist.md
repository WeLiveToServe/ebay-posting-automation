# Orchestrator quality checklist

Use this after `run assemble SKU`, before `run signoff SKU`. Code has already checked the
rules listed under `problems`; this list is for the judgment calls code can't make. Open the
preview (`data/previews/listings.html`, or `/preview`) and the photos.

For each item that fails, send a **specific** correction to the role named. Say what is wrong,
the evidence, and what must change; e.g. "Photo 3 is the copyright page, not the title page:
relabel it" (edition). Avoid vague notes like "check again".

## Edition (edition agent)
- [ ] The title, author, publisher and year match the title page and copyright page in the photos.
- [ ] The edition wording is supported by evidence: a statement, a number line, or the issue
      points. It is not just assumed.
- [ ] Older book: 2 or more sources are listed, with images compared, and any mismatches are
      explained.
- [ ] The seller's notes are reflected and are not contradicted. Any conflict appears under
      contrary evidence.
- [ ] Every photo's label matches what the photo shows, including the volume number.
- [ ] `cover_illustrated` is right: is the front cover pictorial, or plain cloth?

## Condition (condition agent)
- [ ] The grade fits the trade definitions, judged absolutely (not raised for age).
- [ ] Every visible defect is disclosed in its area; nothing is invented.
- [ ] Ex-library, book club and jacket grade are right.
- [ ] The paragraph reads like the Psmith in the City example: positives, wear outside in, verdict.

## Pricing (pricing agent)
- [ ] There are 5 or more eBay prices, or a question explains why not.
- [ ] Every required source has been checked and given a URL, including the zero results.
- [ ] Each comp is marked `same` only when it truly matches the edition. Nearby editions are
      marked `nearby`, come with a weighting question, and the price is set to provisional.
- [ ] The suggested price follows from the `same` comps and the condition. The one-line reason
      says how.
- [ ] None of the comps are the seller's own listings.

## Description (description agent)
- [ ] The blurb is 2–4 sentences: the work first, then this copy's edition and best feature.
- [ ] No condition, price or hype.
- [ ] Every factual claim is supported by the edition facts or is well-established knowledge.
      Nothing is invented.

## The assembled listing (fix through the responsible role, never by hand-editing)
- [ ] The title reads well, states facts only, and puts the most important facts within the
      first 80 characters.
- [ ] Photo order: the lead photo (the cover if illustrated, otherwise the spine) is a good
      gallery image. The angle shots come last.
- [ ] Open questions are real questions for the seller. Answerable lookups are not passed on
      as questions.

A role's re-run makes the later roles that depend on it stale (`run status` shows this). Re-run
those roles as well, then run `run assemble` again. Sign off only when `problems` is empty and
every box above is ticked.
