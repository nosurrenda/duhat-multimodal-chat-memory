# Segment-by-silence experiment

corpus: 7078 messages, 1265 media, 25 channels
questions with a single gold session: 2236

## 0. Gap distribution (does the sweep have anything to sweep?)

| bucket | count |
| --- | --- |
| <=5 min | 6772 |
| 5-60 min  <- entire sweep range | 0 |
| >60 min | 281 |

gaps >5 min that fall *inside* a session: **0**

## Structure per G

| G | segments | == sessions? | median msgs | p90 | max | median speakers | oversized (>40 msgs) | multi-recipient segs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 5 | 306 | no | 19 | 21 | 86 | 2 | 24 | 24/24 (100%) |
| 15 | 306 | no | 19 | 21 | 86 | 2 | 24 | 24/24 (100%) |
| 30 | 306 | no | 19 | 21 | 86 | 2 | 24 | 24/24 (100%) |
| 60 | 306 | no | 19 | 21 | 86 | 2 | 24 | 24/24 (100%) |

### The average hides the only interesting population: split by family

| family | segments | median msgs | max | median speakers | max | median hours | median distinct mention recipients |
| --- | --- | --- | --- | --- | --- | --- | --- |
| dyadic | 281 | 19 | 40 | 2 | 2 | 0.5 | - |
| multiparty | 25 | 71 | 86 | 5 | 8 | 1.8 | 5 |

## Co-location, at the granularity the gold actually supports

Cross-modal Related Retrieval questions: 383
  gold session contains at least one image: 342 (89.3%)
  gold session contains no image at all:    41

## Retrieval: message unit vs segment unit, BM25 only

excluded 246 questions whose gold session is the `converted_from_cross_session` placeholder; 1990 remain

scored on 1990 questions; hit = gold session covered by the retrieved units

| unit | raw@5 | raw@10 | raw@20 | distinct@5 | distinct@10 | distinct@20 |
| --- | --- | --- | --- | --- | --- | --- |
| baseline (message) | 71.1% | 81.6% | 90.7% | 75.0% | 89.6% | 98.9% |
| segment G=5 | 76.0% | 90.6% | 98.9% | 75.9% | 90.3% | 98.9% |
| segment G=15 | 76.0% | 90.6% | 98.9% | 75.9% | 90.3% | 98.9% |
| segment G=30 | 76.0% | 90.6% | 98.9% | 75.9% | 90.3% | 98.9% |
| segment G=60 | 76.0% | 90.6% | 98.9% | 75.9% | 90.3% | 98.9% |

## Where segment and baseline disagree, and where both fail

- both miss at distinct@20: **22**
- segment fixes (baseline misses, segment hits): **0**
- segment breaks (baseline hits, segment misses): **0**

### Failures shared by both units (the real retrieval ceiling)

- Test-Time Learning: 14
- Unimodal Precise Recall: 2
- Cross-modal Related Retrieval: 2
- Answer Refusal: 1
- Multimodal Causal Inference: 1
- Reference & Evolution Tracking: 1
- Conflict Detection: 1

- **[Answer Refusal]** `dyadic_d2/session3` — Not mentioned
- **[Unimodal Precise Recall]** `dyadic_d3/session6` — In the conversation, Zhou Xingchen mentioned how much the boss's social media followers increased from a few thousand before the event?
- **[Cross-modal Related Retrieval]** `dyadic_d3/session6` — Based on the community interface design mentioned in the conversation, which image shows the design centered around the boss's avatar? Provide the session number and image file name.
- **[Multimodal Causal Inference]** `dyadic_d3/session6` — What is the number of boss fans shown in the picture that led Zhou Xingchen to mention the convenience of the mobile interface for users to participate anytime, and to review the growth from
- **[Reference & Evolution Tracking]** `dyadic_d3/session6` — In the conversation, how is the entity of the 'boss's' social media account gradually described and evolved?
- **[Test-Time Learning]** `dyadic_d3/session6` — Based on the newly learned knowledge about pet fan community management from the conversation, which practices from the previous game could Zhou Xingchen draw upon to increase community inte
- **[Conflict Detection]** `dyadic_d3/session6` — The conversation mentions that the boss's account followers increased from a few thousand before the event to nearly fifty thousand, but Su Xiaomeng says there are only thirty thousand follo

### Cases segment broke that the message unit got right
