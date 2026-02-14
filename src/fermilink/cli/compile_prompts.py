from __future__ import annotations


COMPILE_PROMPT_1 = (
    "Please review the file structure of this scientific package, identify where the "
    "source code, examples, docs, testing, and tutorials are. Then, apply the "
    "sci-skills-generator skill at the project root to create the skills/ folder for "
    "this project. We need not only a file or code map, but also enrich the generated "
    "skills/ folder so that ai agents can start from the skills/ folder to optimally "
    "use this package for advanced scientific simulations or computing."
)

COMPILE_PROMPT_2 = (
    "Please review the file structure of this scientific package, identify where the "
    "source code, examples, docs, testing, and tutorials are.  Then, using the skill "
    "at sci-skills-generator/ at the project root to audit whether the skills/ folder "
    "is sufficient for ai agents to optimally use this package for advanced scientific "
    "simulations or computing. If not, please provide the modifications of skills/ "
    "folder accordingly."
)

COMPILE_PROMPT_3 = (
    "please examine whether the skills/ folder contains the file links that are "
    "consistent with the file structure of this code. If not, provide the "
    "modifications accordingly. Then, examine whether the skills/ folder is sufficient "
    "for ai agents to optimally use this package for advanced scientific simulations or "
    "computing, and please enrich the skills/ folder if not."
)
