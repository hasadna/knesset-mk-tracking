# Issue Taxonomy — Ground Truth (v6)

**Status:** v6 taxonomy, team consensus, deployed 2026-07-31. This closed list is
the single source for classifying social posts and mapping bills/vote events.
Pipelines must never invent issue slugs; changes go through this doc, then
`db/seed.sql`.

**How v6 was decided:** the team accepted *both* prior selection principles —
hard vote-measurable topics (v3/"ground truth") *and* discourse axes (v5) — then
synthesized all topics examined so far (v3 list, v5 axes, the pre-ground-truth
15-topic set, and the k-means clusters) into distinct categories with minimal
overlap. `name` holds the short Hebrew UI title (the interface is Hebrew/RTL);
the elaborate title is the first line of `description`.

## The nine issues

| # | Slug | UI title (he) | Short (en) | Elaborate title |
|---|------|--------------|------------|-----------------|
| 1 | `war-peace` | מלחמה ושלום | War & Peace | War, Peace & Regional Security |
| 2 | `oct7-accountability` | מחדל 7 באוקטובר | Oct 7 Inquiry | October 7 Accountability & the State Inquiry |
| 3 | `settlements` | התנחלויות | Settlements | Settlements & the Palestinian Territories |
| 4 | `policing-crime` | פשיעה ושיטור | Crime & Policing | Policing, Crime & Arab Society |
| 5 | `haredi-conscription` | גיוס חרדים | Haredi Draft | Haredi Conscription & Equality of Service |
| 6 | `judiciary` | מערכת המשפט | Judiciary | The Judiciary & the Rule of Law |
| 7 | `discourse-quality` | איכות השיח | Public Discourse | Quality of Public Discourse |
| 8 | `economy-cost-of-living` | כלכלה ויוקר המחיה | Economy & Cost of Living | Economy, Cost of Living & the Welfare State |
| 9 | `infrastructure-services` | תשתיות ושירותים | Infrastructure & Services | Infrastructure, Housing & Public Services |

## Scope notes and merge provenance

1. **War & Peace** — diplomacy↔force axis: Iran/Hezbollah/Hamas, the Gaza war
   and day-after, hostage deals, foreign policy and regional alignments.
   *(Merges v5 `security-peace`, plus regional-security, gaza-day-after and
   foreign-policy from earlier sets.)*
2. **Oct 7 Inquiry** — responsibility for the October 7 failure; establishing,
   shaping, or blocking a state commission of inquiry. *(Identical across v3
   and v5.)*
3. **Settlements** — settlement funding and budgets, settler violence and
   "Jewish terrorism," enforcement asymmetry toward Palestinians, territories
   policy and annexation. *(Merges v3 `settlements-funding` + `settler-violence`
   + the older conflict-territories.)*
4. **Crime & Policing** — police powers and conduct, organized crime (notably
   in Arab communities), illegal weapons, personal security; also Jewish–Arab
   civic equality: budgets, representation, integration. *(v3 `policing-crime`
   + the older jewish-arab/arab-society.)*
5. **Haredi Draft** — draft exemptions, equality of service burden, sanctions
   and service-conditioned benefits. *(The one topic every source produced
   independently; carved out of Religion & State deliberately.)*
6. **Judiciary** — judicial reform, the attorney general, judge selection,
   compliance with rulings, court–government balance. *(v5 axis, retained.)*
7. **Public Discourse** — respectful/substantive vs. derisive/inflammatory
   political speech. Rating-oriented; its bill prompt is deliberately narrow
   (only legislation directly about speech standards, incitement, or personal
   attacks). *(v5 axis, retained as the accepted "fluffy" measure.)*
8. **Economy & Cost of Living** — prices, taxation, state budget, subsidies,
   welfare↔market axis, structural market reforms. *(v5 `economy-welfare` +
   v3 `cost-of-living` + cost-budget + the market half of economic-reforms.)*
9. **Infrastructure & Services** — housing, transport, roads, national AI and
   data projects; health, education and social services. *(v3 `infrastructure`
   + housing-transport + health-welfare + education-employment + the transport
   half of economic-reforms.)*

**Dropped in v6:** Religion & State (non-conscription content now untracked),
Governance/Clean-Government, Civil Liberties/media rights, Netanyahu-leadership
(back to noise, with the campaigning clusters). A procedural-votes catch-all was
considered and rejected — procedural votes simply receive no mapping.

## Classification contract

Each `issue` row carries:
- `name` — short English UI title; `description` — elaborate title (line 1) + scope (line 2);
- `prompt_for_social_post_similarity` — Hebrew yes/no classifier prompt for posts;
- `prompt_for_bill_similarity` — Hebrew yes/no classifier prompt for bills/votes;
- `rating_scale` — JSON mapping `"1"`–`"5"` to Hebrew pole descriptions (UI-facing).
  Value 1 is always the first pole of the axis, value 5 the second.

Summary generation rules are unchanged from v5: `rating` only when evidence
quality is `strong` or `partial`; absence of evidence is never rendered as the
midpoint 3.

Semantic anchors: 8 per issue (4 Hebrew, 2 English, 2 Arabic) in
`data/seed/issue_anchors.json` (version 3), used by the embedding scorer
(`src/mk_tracking/process/embeddings/big_query_to_data.py`) — max-cosine per issue, softmax across issues.

## Reset behavior

v6 replaces v5 rather than renaming it. The one-time
`db/reset_issue_taxonomy_v6_2026-07-31.bq.sql` migration deletes all derived
rows (post_issue, issue anchors, summaries + supporting posts, bill/event
mappings, issue-scoped relations) while preserving source posts, bills, votes,
and MK records. **The full pre-v6 state is snapshotted in the
`mk_tracking_snap_20260731` dataset**, and the UI is temporarily pinned to that
snapshot until v6 scores and summaries are regenerated.
