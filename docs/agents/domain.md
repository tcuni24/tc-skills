# Domain docs

This repository uses a **single-context** layout: one root `CONTEXT.md` for domain terminology and `docs/adr/` for architectural decisions. The skill folders belong to this shared context.

## Before exploring

Read the root `CONTEXT.md` and any ADRs relevant to the area being explored.

If these documents do not exist, proceed silently. Create them lazily through `/domain-modeling` when terminology or decisions actually need recording; setup does not create placeholder domain documents.

## Use the glossary's vocabulary

Use the terms defined in `CONTEXT.md` in specs, issue titles, refactor proposals, hypotheses, and test names. If a needed concept is missing, reconsider the term or note the gap for `/domain-modeling`.

## Respect architectural decisions

If a proposal conflicts with an existing ADR, identify the ADR and explain why the decision should be reopened rather than silently overriding it.
