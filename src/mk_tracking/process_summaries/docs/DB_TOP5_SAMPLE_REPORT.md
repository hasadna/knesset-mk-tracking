# BigQuery top-five retrieval sample

## Configuration

- MK: בנימין נתניהו
- Issues: all 8 current DB issues
- Retrieval: 5 unique highest-confidence `post_issue` posts per issue
- Request shape: one combined Gemini request for the MK
- Posts before retrieval: 81
- Per-issue retrieval slots: 40
- Unique posts after cross-issue deduplication: 36
- Model: `gemini-2.5-flash`
- Successful generation time: 28.247 seconds
- Database writes: none

Artifacts:

- [Top-five candidate](../output/netanyahu-db-top5-sample.json)
- [Top-five checkpoint](../output/db-top5-checkpoints/בנימין-נתניהו.json)
- [Timing data](../output/netanyahu-db-top5-sample.timings.json)
- [Previous all-post candidate](../output/netanyahu-db-sample.json)

## Results

| Issue | All 81 posts | Top-five union | Top confidence |
|---|---:|---:|---:|
| אחריות למחדל 7 באוקטובר | none | none | 0.322 |
| גיוס חרדים | none | none | 0.278 |
| משטרה ופשיעה | partial | partial | 0.536 |
| אלימות מתנחלים | partial | none | 0.379 |
| תקצוב התנחלויות | partial | partial | 0.192 |
| רפורמות כלכליות | none | partial | 0.134 |
| יוקר המחיה | none | none | 0.134 |
| תשתיות | none | none | 0.136 |

## What improved

- The prompt shrank from 81 to 36 posts.
- Successful generation fell from 38.754 to 28.247 seconds, about 27% faster.
- The result no longer stretches anti-accusation tweets into a general position
  on settler violence.
- Every issue still has five candidates available to the model.
- Duplicate `post_issue` rows are collapsed before ranking.
- Cross-topic duplicate posts are sent only once and retain retrieval metadata
  showing every issue for which they were selected.

## Important quality problem

The top-five run produced a false positive for `economic-reforms`. Its highest
retrieval confidence was only 0.134, and the cited post concerns reforming care
for wounded security personnel—not structural economic reform involving local
authorities, public transport, competition, or regulation.

The prompt told Gemini to reject adjacent evidence, but Gemini still classified
this record as `partial`. This demonstrates that top five without a minimum
confidence or deterministic relevance gate can force weak candidates into the
context and create a conclusion.

Recommended correction before a full run:

```text
Retrieve up to five posts per issue, but include only confidence >= 0.20.
If no candidate clears the threshold, emit none without asking Gemini to infer
from weak matches.
```

The exact threshold should be calibrated on several MKs rather than accepted
from this single example. A reasonable comparison set is 0.15, 0.20, and 0.25.

## Validation behavior

The first top-five request returned `strong` for two topics with only one cited
source. The creator rejected that response. The ingestion rule now
deterministically downgrades any one-source `strong` result to `partial`, while
still rejecting unknown sources and more than four citations.

That failed response produced no candidate or checkpoint. The successful retry
is the artifact linked above.
