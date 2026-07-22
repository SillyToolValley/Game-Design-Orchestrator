# Research and Comparables

Use research to answer a current design question, not to decorate a document. The useful output is a better choice, a sharper concern, or a cheaper test.

## Evidence order

Prefer the closest reliable source available:

1. Project files, implementation, telemetry, and direct observations from this game.
2. User-provided goals, constraints, play notes, and business context.
3. Primary external material: the comparable game itself, official rules, platform documentation, developer talks, patch notes, policies, or original research.
4. Credible secondary analysis that shows its method and sources.
5. Community discussion for reported sentiment, examples, and hypotheses only.

Popularity is not proof that a mechanic caused an outcome. A confident source is not necessarily a relevant source.

## Research procedure

1. Write the decision question in one sentence.
2. List what is already known from the project and the user.
3. Identify the missing fact that could change the decision.
4. Search narrowly and prefer primary sources. Check dates for information that can change.
5. Capture the claim, citation, source context, and what it does not establish.
6. Translate the finding into an option, warning, or focused test.

Stop when additional sources are repeating the same useful information or cannot affect the recommendation.

## Claim labels

Use these labels in analysis when the distinction matters:

- `FACT` - directly supported by a cited source or project observation.
- `INFERENCE` - a reasoned interpretation of one or more facts.
- `ASSUMPTION` - useful but currently unsupported.
- `TRANSFER LIMIT` - why a lesson from another context may not hold here.

Example:

> `FACT` The comparable unlocks songs through performance currency. [Official progression guide](https://example.com)
>
> `INFERENCE` This likely gives skilled players a reason to replay earlier songs.
>
> `TRANSFER LIMIT` Its audience, catalog size, and paid unlock policy differ from this project, so it does not establish acceptable grind here.

For local evidence, cite a file and section. For web evidence, use a descriptive link and record the access date in `references.md` when the source will be reused.

## Comparable analysis

Compare only dimensions related to the decision:

| Dimension | Questions |
|---|---|
| Intended experience | What should the player feel, notice, or decide? |
| Player and context | Who plays, where, for how long, and with what prior knowledge? |
| Repeated loop | What action, feedback, consequence, and next choice repeat? |
| Supporting structure | Which progression, social, content, economy, or interface system enables it? |
| Tradeoff | What complexity, burden, exclusion, or exploit does it introduce? |
| Evidence | Is the claimed effect observed, reported, measured, or merely inferred? |
| Transfer | Which differences make copying the lesson risky? |

Extract a principle in project language. Avoid copying a surface feature merely because another game has it. Prefer: the comparable suggests a principle under specific conditions; here are two ways to test that principle within this project's constraints.

## Source hygiene

- Cite the exact source that supports the nearby claim.
- Quote sparingly and preserve the source's meaning.
- Separate official intent from observed player response.
- Record disagreement between sources instead of hiding it.
- Say `unknown` when the evidence does not answer the question.
- Never fabricate market numbers, player quotes, citations, or hands-on experience.
