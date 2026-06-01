# FermiLink Drvloop Guide

You are running in FermiLink derivation loop mode. Each round should advance one
major analytical step, update persistent artifacts, and leave the next round a
clear verifier-aware handoff.

## Operating rules

- Read `projects/memory.md` before acting.
- Work under the active `projects/YEAR-MM-DD-NAME` directory named in the prompt, where `NAME` is a short name for representing this derivation.
- Treat `derivation_spec.yaml` as the locked problem statement. Do not silently
  weaken the target, assumptions, or non-goals. If the statement appears wrong
  or ambiguous, write a separate spec-amendment note and continue against the
  locked spec until the user accepts a change.
- Save detailed algebra, equation transformations, failed routes, numerical
  checks, proof sketches, LaTeX, and review notes under the active project
  directory.
- Keep `projects/memory.md` compact: only major done steps, major needed steps,
  and major conclusions.
- Treat `fermilink-drvloop/workflow_state.json` as a hard process gate. A
  validation-clean final manuscript is not enough for publication-depth work;
  route population, pathway development, synthesis, review, numerical checks,
  and final packaging must also be complete.
- If the current route is hopeless, record why and move to a different route in
  a later round.
- Use explicit `route_id` values in proof obligations when exploring multiple
  pathways. This lets the controller score and revisit routes separately.

## Proof obligations

Maintain `proof_obligations.yaml` in the active project. Every nontrivial claim
that supports the final result should have an obligation entry, especially:

- algebraic identities and commutator/operator identities;
- limits, asymptotic reductions, perturbation-order claims, and approximations;
- dimensional or unit-consistency claims;
- numerical spot checks for major analytical results;
- imported theorems, textbook facts, or literature-backed equations;
- final LaTeX build checks when manuscripts are ready;
- optional formal checks such as Lean snippets for small mathematical kernels.

Use this YAML shape:

```yaml
obligations:
  - id: algebra-main-1
    type: algebra
    claim: "The simplified residual is zero."
    lhs: "(x + 1)**2"
    rhs: "x**2 + 2*x + 1"
    symbols: [x]
    covers_target_claims: [target-1]
    critical: true
```

Supported `type` values include `algebra`, `commutator`, `limit`, `dimension`,
`numeric_check`, `citation`, `assumption`, `latex_build`, and
`formal_optional`. Domain-specific validators also accept `trace_preservation`,
`hermiticity`, `tensor_index`, `bch_order`, `perturbation_order`, and
`conservation`. Use `covers_target_claims` when a strong mechanical or
reviewer-backed obligation establishes a locked target claim. Broad `citation`,
`derived_here`, `manual`, `assumption`, `latex_build`, or final-artifact
obligations do not count as target coverage by themselves. Final completion
requires granular algebra, limit, dimensional, commutator, numerical, formal, or
domain-specific obligations for the target claims.

For numerical scripts, place the script under `projects/`, reference it with a
relative path, and set `trusted: true` only when it is safe for the controller to
run. For literature-backed claims, either provide `derived_here: true` or a real
`citation`/`source`, not a placeholder.

The controller automatically scans manuscript-like artifacts for displayed
equations, assumptions, citation placeholders, hidden-lemma phrases, and
therefore/hence-style transitions. If these auto-extracted reviewer obligations
appear in the prompt, either add explicit validating obligations with
`source_file` pointing at the artifact or revise the artifact.

## Search workflow

For publication-depth drvloop runs, follow this staged process unless the
workflow prompt explicitly says a lighter profile is active:

1. Write a route-population artifact, usually `00_pathways.md`, with at least
   ten plausible derivation pathways. For each route list its starting
   equations, assumptions, expected validation kernels, risks, and why it might
   fail.
2. Explore one major pathway per round in a dedicated artifact such as
   `01_pathway1_*.md`. The controller expects developed route artifacts, not
   only a compact route list.
3. Record proof obligations while deriving, not only after the manuscript is
   done. Keep route IDs stable.
4. Rank or compare the route population using target coverage, unresolved
   obligations, unsupported assumptions, and numerical/verifier evidence.
5. Write an official synthesis artifact that chooses the cleanest route or
   combines independent routes.
6. Draft the final manuscript only after synthesis. It should be self-contained
   and equation-rich enough for a physics or chemistry preprint.
7. Write a gap review before finalization. Search for hidden lemmas,
   unjustified transitions, assumption drift, and missing citations.
8. Add numerical or limiting-case checks for the major analytical claims.
9. Write the pedagogical note with intermediate derivation details.
10. Finish with a final consistency review that checks the manuscript, note,
    obligations, numerical evidence, and locked spec together.

After the derivation workflow is final-ready, the drvloop controller may launch
a separate final publication sweep. That sweep is responsible for polished
APS-style LaTeX/PDF exports and should not be mixed into the normal derivation
stages.

If `workflow_state.json` reports an open `next_stage`, work on that stage
instead of attempting final completion.

## LaTeX and review

- For every nontrivial equation beyond entry-level graduate material, either
  derive it in the manuscript or record a reliable citation obligation.
- The final manuscript should be self-contained and suitable for a physics or
  chemistry preprint. Publication-depth runs should first finish the analytical
  manuscript and note in the normal derivation workflow; polished LaTeX/PDF
  exports belong to the controller's final publication sweep.
- In a fresh round before final completion, review the manuscript for gaps,
  assumption errors, hidden changes to the problem statement, and invalid
  equation transitions. Fix issues and update obligations.
- Provide a supplementary pedagogical note with intermediate derivation details
  when the task calls for a publication-style result.

## Stop criteria

Output exactly:

```xml
<promise>DONE</promise>
```

only when all of the following are true:

- the requested derivation is complete;
- `proof_obligations.yaml` covers every locked target claim;
- the validation report is validation-ready with no failed or unresolved
  critical obligations;
- `fermilink-drvloop/workflow_state.json` is workflow-ready and quality-ready
  for the active proof-depth profile;
- the final manuscript and requested supplementary note are written. By default
  the runner requires final manuscript and pedagogical-note artifacts under the
  active project unless `derivation_spec.yaml` explicitly relaxes that
  requirement;
- if the controller has started the final publication sweep, it has produced
  the requested APS-style LaTeX/PDF exports;
- LaTeX build obligations are included when relevant.

The runner will withhold completion if validation or workflow gates are not
final-ready.
