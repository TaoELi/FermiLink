# FermiLink Drvloop Guide

You are running in FermiLink derivation loop mode. At each round of agent reasoning, due to the limit of context window, do not try to resolve everything and only focus on one key step. Later agent rounds will continue this quest.

## Operating rules

- Read `projects/memory.md` before acting.
- For a new derivation, work under `projects/YEAR-MM-DD-NAME`, where `NAME` is a brief name of this task.
- Save detailed algebra, equation transformations, failed routes, and checks under `projects/YEAR-MM-DD-NAME`.
- Keep `projects/memory.md` compact: only major done steps, major needed steps, and major conclusions.
- Stop at this round if you find the contiuned derivation is hopeless; next round will contiune your quest.

## Starting

- At the start of a new derivation work, creatively provide 10 different pathways for derivations.

## Later Agent Rounds

- In each agent round after the starting pathway generation, focus on each signle major derivation pathway and provide a detailed step-by-step derivation, with all major assumptions properly provided.

- After each pathway is explored in detail, stop, then in a fresh agent round, learn from the advantages and disadvantages of each pathway, provide the most natrual, elegant, robust derivation as the final official derivation for the task.

## LaTeX writing rules

- After the whole derivation is double checked for correctness, for every nontrival equation introduced beyond the entry-level graduate student level, try to search textbooks or peer-reviewd papers to insert correct citations. If this equation is derived by you, provide a detailed explanation of the assumptions and your reasoning.

## Numerical calculation rules

- For major analytical results, if possible, use numerical calculations to double check;
- Supply the numerical calculation results together with the final latex document when possible.

## Stop criteria

- When the requested derivation is complete and double-checked for correctness, a revtex preprint sytle latex document for the derivation is written (and compiled to pdf if possible), output exactly:

```xml
<promise>DONE</promise>
```

- The final manuscript should exceed the quality of standard Phys Rev A/B or J Chem Phys quality and be self-contained and publication ready.