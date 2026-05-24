# FermiLink Drvloop Guide

You are running in FermiLink derivation loop mode. At each round of agent reasoning, due to the limit of context window, do not try to resolve everything and only focus on one key step. Later agent rounds will continue this quest.

## Operating rules

- Read `projects/memory.md` before acting.
- For a new derivation, work under `projects/YEAR-MM-DD-NAME`, where `NAME` is a brief name of this task.
- Save detailed algebra, equation transformations, failed routes, and checks under `projects/YEAR-MM-DD-NAME`.
- Keep `projects/memory.md` compact: only major done steps, major needed steps, and major conclusions.
- Stop at this round if you find the contiuned derivation is hopeless; next round will contiune your quest.
- Follow the step-by-step guides below. Each section below should use at least one fresh agent round.

## 1. Start

- At the start of a new derivation work, **creatively** provide 10 different pathways for derivations.

## 2. Exploring EVERY Pathway

- In each agent round after the starting pathway generation, focus on each signle major derivation pathway and provide a detailed step-by-step derivation, with all major assumptions properly provided.

- ENSURE every pathway is explored in detail.

## 3. Summarized Pathway

- After each pathway is explored in detail, stop, then in a fresh agent round, learn from the advantages and disadvantages of each pathway, provide the most natrual, elegant, robust derivation as the final official derivation for the task.

## 4. LaTeX writing rules

- After the whole derivation is double checked for correctness, for every nontrival equation introduced beyond the entry-level graduate student level, try to search textbooks or peer-reviewd papers to insert correct citations. 

- If this equation is derived by you, provide a detailed explanation of the assumptions and your reasoning.

- The final manuscript should exceed the quality of standard Phys Rev A/B or J Chem Phys quality and be self-contained and publication ready.

## 5. Review for gaps and invalid derivations

- In a fresh round, examine whether the LaTeX manuscript has any gap, assumption error in derivations or whether if there is anything wrong between every step of derivations, and then fix them. 

## 6. Numerical calculation rules

- For major analytical results, if possible, use numerical calculations to double check;

- Supply the numerical calculation results together with the final latex document when possible.

- For disagreed numerical vs analytical results, dig deeply to figure out the issue. Fix either numerical or analytical derivations.

## 7. Supplementary pedagogical step-by-step enrichment rules

- After all the above steps, in a fresh agent round, provide a supplementary pedagogical note which is an extended version of the latex manuscript.

- Provide detailed derivation between every equation in the latex manuscript;

- For every introduced new equation not derived in the manuscript, either provide a comprehensive derivation or provide the reliable citation for user to check;

- Finalize this pedagogical step-by-step note to ensure senior undergraduate students in this major or first-year graduate students can understand properly.

- This note should also be prepared as a latex document with pdf output.

- Continue to next step ONLY when this pedagogical step-by-step note can be properly understood by senior undergraduate students in this major or first-year graduate students.

## 8. Stop criteria

- When and only when a) the requested derivation is complete and double-checked for correctness, b) a revtex preprint sytle latex document for the derivation is written (and compiled to pdf if possible), and c) the supplementary pedagogical enriched latex note is provided, output exactly:

```xml
<promise>DONE</promise>
```

- Then stop the whole derivation workflow.
