You are a senior software engineer performing an independent review of FinScope.

## Review area

**[REVIEW AREA]**

Review only the following scope:

**[REVIEW SCOPE]**

Do not modify the application code, tests, configuration, documentation, or other project files.

The only permitted changes are:

* create a `code_review` directory at the project root if it does not already exist;
* write the complete review to `code_review/[OUTPUT FILE].md`.

Do not overwrite or modify unrelated files already present in `code_review`.

## Review approach

First, inspect `AGENTS.md`, the relevant project structure, established conventions, and enough surrounding code to understand how the reviewed area interacts with the rest of the application.

Approach the project with a fresh pair of eyes. Evaluate it as production software that must remain understandable and maintainable by a human developer, even though much of it was developed through AI-assisted coding.

Run relevant read-only commands, tests, linters, static-analysis tools, or inspection tools when useful. Do not implement corrections, automatically reformat files, generate new application files, or make configuration changes.

Pay particular attention to:

* correctness and potential defects;
* maintainability and clarity;
* unnecessary complexity;
* redundant or duplicated code;
* dead, obsolete, legacy, compatibility, transitional, or abandoned code;
* inconsistent implementations of the same behavior;
* violations of established project conventions;
* inconsistent naming, structure, and coding style;
* weak separation of responsibilities;
* inappropriate coupling or leakage between components or layers;
* missing validation and error handling;
* fragile behavior that currently works but is difficult to maintain or extend;
* security, privacy, reliability, and performance risks, where relevant;
* missing or inadequate tests directly related to the reviewed area;
* missing, misleading, or obsolete documentation directly related to the reviewed area;
* opportunities to simplify the implementation without overengineering it.

For this review, also examine the following scope-specific concerns:

**[SCOPE-SPECIFIC CONCERNS]**

Do not treat the scope-specific concerns as an exhaustive checklist. Inspect the implementation independently and report other material issues you discover.

Do not assume that FinScope should use a particular architecture, framework pattern, abstraction, or design technique. Recommend changes only when they address a concrete correctness, security, reliability, performance, usability, or maintainability problem.

Account for FinScope's actual size, purpose, technology stack, deployment model, and single-developer maintenance context.

Do not report purely hypothetical issues without explaining a realistic failure mode or maintenance cost. Do not inflate the report with trivial formatting preferences or subjective style opinions unless they reveal a broader inconsistency.

## Findings

For every finding, provide:

1. **Title**
2. **Severity:** `CRITICAL`, `IMPORTANT`, or `NICE TO HAVE`
3. **Classification:** confirmed defect, probable risk, maintainability concern, or optional improvement
4. **Location:** precise file path, class, function, template, script, configuration entry, database object, or other relevant location
5. **Evidence:** what you observed in the repository
6. **Impact:** why it matters in practice
7. **Recommendation:** the intended direction of the correction, without implementing it
8. **Scope:** whether the issue is isolated or occurs in multiple locations

Use these severity definitions:

* **CRITICAL:** A confirmed or highly probable issue involving data loss, data corruption, security or privacy exposure, major functional failure, broken deployment, materially incorrect results, or an architectural problem that makes important changes unsafe.
* **IMPORTANT:** A material correctness, reliability, maintainability, testing, performance, usability, or design problem that should be addressed, but does not currently constitute an immediate critical failure.
* **NICE TO HAVE:** A worthwhile cleanup, simplification, consistency improvement, minor optimization, or maintainability enhancement with limited immediate impact.

Order findings by severity and then by expected impact.

Consolidate repeated instances of the same underlying problem into one finding, while listing representative locations. Do not repeat the same issue under multiple headings.

Distinguish clearly between:

* confirmed defects;
* probable risks;
* maintainability concerns;
* optional improvements.

When a finding depends on an assumption, state the assumption explicitly. If the available evidence is insufficient, report the uncertainty rather than presenting the issue as confirmed.

## Review summary

At the end of the report, include:

* the number of findings by severity;
* the five highest-priority findings;
* recurring root causes or patterns;
* the main files, modules, and areas inspected;
* areas inspected where no material issue was found;
* commands, tests, linters, or analysis tools executed;
* anything that could not be assessed confidently and why;
* relevant assumptions made during the review.

Write the complete review report in Markdown to:

```text
code_review/[OUTPUT FILE].md
```

Do not implement corrections or prepare a detailed implementation plan yet.

In your final response, provide only:

* a brief confirmation that the review is complete;
* the path to the review file;
* the number of findings by severity.
