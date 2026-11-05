# OpenPoke routing demo

A follow-up can depend on something a previous execution agent remembers,
like which draft Security approved. The original interaction agent saw
every worker name on every turn. In this version, it sees a shortlist
from keyword search over names and one-sentence summaries. It can refine
that search when it needs more evidence. Once it chooses a worker, that
worker loads its own execution history.

## What happened in the benchmark?

We compared the original full-roster baseline with lookup on two suites.
The same-entity suite has several plausible drafts per case. The generated
summary suite uses summaries workers wrote before seeing the follow-ups.
Both versions used GPT-6 Luna, the same simulated mailboxes and histories,
and the same behavior for ending a turn on `wait`.

| Large-roster metric | Full-roster baseline | Lookup |
| --- | ---: | ---: |
| Correct replies | 5/6 | 6/6 |
| Exactly one dispatch to the expected worker, when dispatch was needed | 3/5 | 5/5 |
| Total input tokens on the five pairs both answered correctly | 705,492 | 167,981 |

The large runs added 1,500 dormant workers to each fixture roster. The
expected worker was in the middle. The totals include both agent roles
and show about **76% fewer input tokens** for lookup. Mina's repeat
request belongs in the reply score but does not require a dispatch. Both
versions answered all six small-roster cases correctly.

Northstar is a good case to show. The user asked for the incident update
Security cleared after a customer call. Baseline consulted both the recap
worker and the approval worker. Lookup found the approval worker and
dispatched once. Both showed the right draft. The first correct reply
was recorded at 16.10 seconds for baseline and 7.89 seconds for lookup
in this pair. That timing excludes browser polling and is one measurement.

Orion shows a different failure. In the same-entity suite, baseline chose
an archive worker and never showed the approved draft. Lookup chose the
Finance approval worker and showed the approved wording.

These are results from one paired sweep. The largest lookup shortlists
still contained about 250 candidates, so the improvement depends on how
well search can narrow the roster. A repeat run can yield different
model decisions and timings.

## How do I run it?

From the repository root, install the Python dependencies from
`server/requirements.txt` in `.venv` and set `OPENROUTER_API_KEY` in
the environment or the local `.env` file. Use a full clone containing
baseline commit `5b5f635`. The runner archives that commit and applies
the same `wait` termination behavior to its temporary copy.

Create the snapshots if you do not already have them. These commands
use live models to generate the prior work against simulated Gmail.

```sh
mkdir -p results

.venv/bin/python tests/prepare_routing_eval.py \
  --setup-file tests/fixtures/workload_approval_setup.json \
  --model openai/gpt-6-luna \
  --output results/routing-workload-approval-snapshot.json

.venv/bin/python tests/prepare_routing_eval.py \
  --model openai/gpt-6-luna \
  --output results/routing-generated-snapshot.json
```

For a short demo, run Northstar with the large roster. This makes two
live runs, one per version, and requires no Gmail account.

```sh
.venv/bin/python tests/run_routing_eval.py \
  --cases-file results/routing-workload-approval-snapshot.json \
  --case workload-northstar-security \
  --interaction-model openai/gpt-6-luna \
  --execution-model openai/gpt-6-luna \
  --large-extra-workers 1500 \
  --target-position middle \
  --output results/demo-northstar.jsonl
```

For the full comparison, remove `--case workload-northstar-security`
and add `--include-small`. Then run it again with
`--cases-file results/routing-generated-snapshot.json` and a different
output filename. Each suite runs 12 evaluations. The runner appends
results, so choose fresh output names when repeating a comparison.

Read `task_success` for draft correctness, `exact_dispatch_success` for
worker selection, and `first_correct_reply_seconds` for server-side
latency. Add `interaction_input_tokens` and `execution_input_tokens` to
compare total input cost. For Mina's repeat request, a correct reply with
no dispatch is the desired result.

The original local traces are
`results/routing-eval-workload-current-1500.jsonl` and
`results/routing-eval-generated-current-1500.jsonl`. The snapshots and
traces under `results/` are gitignored. A new clone can generate its own
snapshots using the commands above.

For the offline correctness suite:

```sh
.venv/bin/python -m unittest discover -s tests -v
```
