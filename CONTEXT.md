# Media tracking

A personal record of movies and whole TV series, with optional connected-service progress and comparison with followed people. The established domain glossary is in [GLOSSARY.md](../docs/media-tracker/GLOSSARY.md); this file records terminology resolved during the maturity work.

## Language

**Manual tracking**:
Maintaining a personal list entry directly, including finding a title, adding it, and recording status, progress, or a rating, regardless of where the media was watched.
_Avoid_: Offline tracking, unsupported user

**Connected tracking**:
Maintaining personal viewing progress using observations from an optional linked service, with the user retaining control over their own entries and ratings.
_Avoid_: Mandatory sync, automatic rating

**Catalog search**:
Finding movies or whole TV series available to add or inspect, including titles absent from the user's personal list.
_Avoid_: List filter

**List search**:
Finding entries within the particular personal list being viewed.
_Avoid_: Catalog search

**Friend comparison**:
Comparing one's own media tracking with the eligible information shared by people one follows; following need not be mutual.
_Avoid_: Mutual-friend requirement

**Profile presentation**:
The profile owner's choice of combined or separate media lists, shared by all viewers of that profile.
_Avoid_: Viewer layout preference

**Default list sort**:
The viewing user's preferred ordering of entries, applied to lists they view without changing the profile owner's presentation.
_Avoid_: Profile owner's sorting mandate

**Daily progress activity**:
A profile activity summarizing a person's progress on one title during one UTC calendar day, even when viewing of other titles interleaves.
_Avoid_: One card per episode

**Pending connection update**:
One title-level operational delivery record for a local change intended for one or more connected services. It remains pending while any applicable provider delivery is pending or failed and clears only after all succeed.
_Avoid_: One card per provider, successful local save

**Season position**:
The latest watched regular episode identified by its season and episode number, such as S1E4, alongside the completed-season progress.
_Avoid_: Whole-show episode number

**Finished season**:
A season that is fully released and whose episodes have all been watched; an airing season with every currently released episode watched does not meet this definition.
_Avoid_: Caught up

**Hidden attention prompt**:
A temporarily suppressed on-screen prompt whose underlying notification may still require action.
_Avoid_: Resolved notification

**Activity time**:
The time a tracking update is published, distinct from the title's viewing, start, or finish date and from the time a visitor opens the feed.
_Avoid_: Viewing date, page-view time
