# Ops Inbox: evaluation report

Model `deepseek/deepseek-v4.1-flash-1017`, confidence threshold **0.45** (chosen on the tuning split only). All numbers below come from `make evaluate`, which replays recorded model answers; no number was tuned on the held-out split.

## Held-out set (100 e-mails, never used for tuning)

| Measure | Held-out | Tuning |
|---|---|---|
| Classification accuracy | 99.0% (98 scored) | 100.0% |
| Handled without a person | 28 of 100 (28.0%) | 84 of 200 (42.0%) |
| ... of the e-mails that could be | 57.5% | 88.4% |
| Wrong automatic e-mails | 1 (3.57 per 100 automatic) | 0 (0.0 per 100) |
| Wrong fields in automatic e-mails | 0.0 per 100 | 0.0 per 100 |
| Adversarial e-mails stopped | 10 of 10 | 18 of 18 |
| Model cost per e-mail | $0.0005 | $0.0005 |
| Model calls per e-mail | 4.56 | 4.62 |
| Model time per e-mail | 4.7 s | 6.01 s |

## Field accuracy on the held-out set

| Field | Correct | Accuracy |
|---|---|---|
| Car | 67 / 67 | 100.0% |
| Customer name | 63 / 63 | 100.0% |
| Phone | 63 / 63 | 100.0% |
| Extras | 61 / 63 | 96.8% |
| Invoice: invoice date | 12 / 12 | 100.0% |
| Invoice: invoice number | 12 / 12 | 100.0% |
| Invoice: lines | 12 / 12 | 100.0% |
| Invoice: subtotal | 12 / 12 | 100.0% |
| Invoice: supplier name | 12 / 12 | 100.0% |
| Invoice: tax | 12 / 12 | 100.0% |
| Invoice: total | 12 / 12 | 100.0% |
| Licence: classes | 60 / 60 | 100.0% |
| Licence: date of birth | 60 / 60 | 100.0% |
| Licence: expires on | 60 / 60 | 100.0% |
| Licence: holder name | 59 / 60 | 98.3% |
| Licence: licence number | 59 / 60 | 98.3% |
| Pick-up date | 66 / 66 | 100.0% |
| Pick-up place | 62 / 63 | 98.4% |
| Pick-up time | 66 / 66 | 100.0% |
| Purpose | 62 / 63 | 98.4% |
| Quoted total | 63 / 63 | 100.0% |
| Return date | 64 / 66 | 97.0% |
| Return time | 66 / 66 | 100.0% |

## Classification (held-out confusion matrix, rows are the truth)

| | cancellation | change | new_booking | not_relevant | question | supplier_invoice |
|---|---|---|---|---|---|---|
| cancellation | 5 | 0 | 0 | 0 | 0 | 0 |
| change | 0 | 7 | 0 | 0 | 0 | 0 |
| new_booking | 0 | 0 | 63 | 0 | 0 | 0 |
| not_relevant | 0 | 0 | 0 | 5 | 0 | 0 |
| question | 0 | 0 | 0 | 1 | 5 | 0 |
| supplier_invoice | 0 | 0 | 0 | 0 | 0 | 12 |

## Straight-through rate against errors (held-out)

| Threshold | Automatic | Wrong automatic | Per 100 automatic |
|---|---|---|---|
| 0.0 | 51 | 4 | 7.84 |
| 0.1 | 51 | 4 | 7.84 |
| 0.2 | 51 | 4 | 7.84 |
| 0.3 | 48 | 1 | 2.08 |
| 0.4 | 28 | 1 | 3.57 |
| 0.5 | 28 | 1 | 3.57 |
| 0.6 | 28 | 1 | 3.57 |
| 0.7 | 28 | 1 | 3.57 |
| 0.8 | 28 | 1 | 3.57 |
| 0.9 | 28 | 1 | 3.57 |
| 1.0 | 27 | 1 | 3.7 |

## Reading: vision model against local OCR

Spike on ten degraded licences (`reports/reading_spike.json`):

| Reader | Field accuracy | Scans | Photos | Confident and wrong |
|---|---|---|---|---|
| tesseract | 78.0% | 100.0% | 56.0% | 1 |
| vision | 100.0% | 100.0% | 100.0% | 0 |
| vision checked by tesseract | 100.0% | 100.0% | 100.0% | 0 |
| tesseract checked by vision | 78.0% | 100.0% | 56.0% | 0 |

Held-out attachments, by quality (vision reader against local OCR only):

| Attachment | Vision | Local OCR |
|---|---|---|
| invoice:pdf | 100.0% | 100.0% |
| invoice:scan | 100.0% | 100.0% |
| licence:clean | 95.6% | 100.0% |
| licence:photo | 100.0% | 56.8% |
| licence:scan | 100.0% | 94.3% |

## What limits these numbers

- **Phone photos.** The second extraction pass reads local OCR text on purpose, so that a vision misread shows up as a disagreement. On phone photos local OCR is weak and the readings disagree often, which stops correct e-mails for review. This is most of the gap between the tuning and the held-out straight-through rates.
- **A flaw in the generated set.** Clean booking requests got their licence format by position (clean, scan, photo in turn) and the held-out split takes every third e-mail, so every held-out clean booking came with a phone photo, while the tuning clean bookings had none. Found after the held-out run; reported rather than reshuffled.
- **The wrong automatic e-mail.** One held-out customer question was sorted as not relevant and archived without a person (no adversarial e-mail got through). Every decision is in `reports/predictions-heldout.jsonl`.
- **Synthetic data.** E-mails were worded by a small model around facts set by code; real mail is messier. The set has 300 e-mails, so one e-mail moves a held-out percentage by a whole point.
