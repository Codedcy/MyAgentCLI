# Brainstorming Frameworks Reference

This document provides structured frameworks to guide the brainstorming process.

## SCAMPER Method
- **S**ubstitute: What can be replaced?
- **C**ombine: What can be merged?
- **A**dapt: What can be adjusted or borrowed?
- **M**odify / Magnify: What can be changed or amplified?
- **P**ut to another use: What else can it be used for?
- **E**liminate: What can be removed or simplified?
- **R**earrange / Reverse: What order or direction changes could help?

## Design Trade-off Dimensions
When evaluating alternatives, weigh along these axes:

| Dimension | Low | High |
|-----------|-----|------|
| Complexity | Simple, easy to understand | Requires deep expertise |
| Flexibility | Fixed, opinionated | Highly configurable |
| Performance | May have bottlenecks | Optimized for scale |
| Maintainability | Easy to change | Coupled, hard to refactor |
| Time to Ship | Can deliver quickly | Long build time |
| Risk | Proven patterns | Novel, unvalidated approach |

## Clarifying Question Framework
Always ask these before proposing solutions:
1. **Goal**: What is the desired outcome? How will success be measured?
2. **Context**: What constraints exist? (budget, time, tech, team)
3. **Users**: Who will use this? What is their skill level?
4. **Scale**: How much data/traffic/usage is expected?
5. **Risks**: What are the worst-case failure modes?
6. **Alternatives**: What has already been tried or considered?

## Common Design Patterns
- **Strategy Pattern**: Encapsulate interchangeable algorithms
- **Observer / Pub-Sub**: Decouple event producers from consumers
- **Adapter**: Bridge incompatible interfaces
- **Repository**: Abstract data access behind a collection-like interface
- **Command**: Encapsulate operations as objects (undo, queue, log)
- **Pipeline**: Chain processing stages with well-defined inputs/outputs

## Anti-Patterns to Avoid
- **Gold Plating**: Adding features nobody asked for
- **Premature Optimization**: Optimizing before measuring
- **Big Design Up Front**: Designing everything before building anything
- **Not Invented Here**: Rebuilding what open-source already solves
- **God Object**: One class that knows and does too much
