# Why change how OpenPoke picks an execution agent?

The interaction agent used to get a list of every execution agent on every
turn. The list was called `<active_agents>`, but the names stayed there
after the work was done. If OpenPoke created 1,500 workers over time, a new
request could see all 1,500 names even if it only needed one.

The obvious fix was to remove the list. That works for "Did Keith reply
to the proposal I sent Tuesday?" since a fresh worker has enough to go on.
It gets harder with "Did they ever get back to us about the exception?"
The user remembers what they asked for, but the details that identify it
might only exist in the old worker's history. I wanted to keep a way to
get back to that worker without putting the full roster in the prompt.

## What did we assume?

- **User prompts are usually short and conversational.** We expect a
  few words to carry most of the useful search clues, like the person,
  project, or decision the user is asking about.
- **A worker's history is worth reusing.** A worker name identifies a
  history. A new run under that name can recover the draft ID or decision
  that made the earlier work useful.
- **A short summary can carry enough clues to find the worker.** The
  summary should say what the worker did and preserve details the user
  might use later. "Checked for a reply" is much less useful than "Checked
  for a reply to Jai's renewal fee-waiver request."
- **Keyword overlap can get us to a useful set of candidates.** If the
  user says Jai, an exact hit on Jai should matter. Word overlap doesn't
  resolve every follow-up, especially when the user and the summary use
  different words.
- **Routing and task success need separate scores.** A fresh worker can
  find the right draft and still miss a chance to reuse prior work. An
  extra dispatch can also cost time even if the final answer is right.

## What did we build?

The [roster](server/services/execution/roster.py) stores a name and an
optional summary per worker. It can still read the old list of names.
The [execution runtime](server/agents/execution_agent/runtime.py) asks
for a routing summary in the same final response as the task result.
It saves the summary separately and keeps the previous one if the update
is missing or invalid. The execution log still holds the detailed history.

On a new user turn, the [prompt builder](server/agents/interaction_agent/agent.py)
searches using the latest message and includes the matching candidates.
It can also provide a separate hint for a recent, unambiguous worker
result. If the user asks to see an exact draft again and the draft is
already in the conversation, the interaction agent can show it directly.

The [search](server/services/execution/search.py) matches whole terms in
both names and summaries. It counts each query term once. When one word
matches far fewer summaries than a broader word, the search uses the
rarer match to filter out weaker candidates. It preserves the strongest
keyword matches, then includes the top three and all ties at the cutoff.
This is why a shortlist can contain more than three workers.

Search and dispatch are separate. The interaction agent chooses the
worker after seeing the summaries and can reformulate a search when it
can't find a good match. Dispatch explicitly says whether to reuse a
worker or create a new one. Reuse keeps the existing history. An unknown
reuse ID fails without creating a worker.

## Why not start with semantic search?

Consider "Has Vijay responded to my latest email?" and "Has Jai responded
to my latest email?" The requests have almost the same meaning, but the
name is what tells us which worker to reuse. If the worker summary
preserves the name, a whole-word match gives us an explicit signal.
Embedding similarity can distinguish the two, but it doesn't guarantee
that the recipient will decide the ranking.

We already ask each worker for one sentence about what it did. If that
sentence keeps the details the user is likely to mention again, we can
scan the roster directly. We also get to see which words matched when
something goes wrong.

## What did we try along the way?

- **Search only when the model asks for it.** Luna sometimes recognized a
  follow-up and still created a worker without searching. Automatic lookup
  gives it candidates before it makes that decision.
- **A hard top-three cutoff.** On Northstar, equally scored archive
  workers could push the expected worker out alphabetically. Expanding
  ties fixed that failure mode, but broad matches could put hundreds of
  workers in the prompt. Distinctive-term filtering helped when the query
  included a name like Orion or Noor. Searching worker names as well as
  summaries recovered a clue the Northstar summary had omitted.
- **Semantic reranking.** We tried `BAAI/bge-reranker-base` on 255 saved
  Northstar candidates. It ranked the expected worker 193rd on the raw
  request and 251st on Luna's search phrase. A hand-written query that
  emphasized Security's approval ranked it first. That's a useful lead,
  but the model-generated query didn't work well enough to ship.
- **Letting the model decide when it was done waiting.** Luna repeatedly
  called `wait` while a worker ran in the background. A successful
  `wait` now ends the turn after the current tool batch. The worker
  result starts a separate turn. The benchmark applies the same behavior
  to the baseline so the comparison doesn't count this as a lookup win.

## What did the tests show?

We used a simulated mailbox and the same worker histories for both
versions. The worker-written summary suite saves the original work before
asking the follow-up. The same-entity suite puts several drafts in the
mailbox that look alike. A draft listing can show the versions, but the
worker's history is what says which version was approved.

In the paired sweep with 1,500 extra workers, baseline showed the correct
draft in 5 of 6 cases and lookup did so in 6 of 6. For the 5 requests
that needed a worker, baseline dispatched exactly once to the expected
worker in 3 cases and lookup did so in all 5. On the 5 pairs where both
versions answered correctly, total input tokens across both agent roles
went from 705,492 to 167,981, about 76% fewer. Both versions got all 6
small-roster requests right.

This shows a substantial context saving and better worker selection on
these cases. The dormant workers are synthetic and the live model can
make different choices on a rerun. The [demo guide](DEMO.md) has the
commands and explains how to read the saved results.

## What would I do next?

1. Repeat the same-entity runs with the same snapshots. Check time to
   the first correct reply as well as dispatch count. More dispatches
   are extra work, but the user only feels it if the answer takes longer
   or the interaction gets noisier.
2. Try more histories from repeated work on the same project or account.
   Let the workers write their own summaries and keep the follow-ups
   hidden until setup is finished. Check shortlist recall before blaming
   the model for a bad dispatch.
3. Revisit reranking only if a small offline test shows it can keep the
   right worker near the top on multiple cases, using queries the
   interaction agent actually writes. Include the reranker's latency
   when comparing the live versions.
