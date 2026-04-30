---
name: one-focused-question
description: When you must ask the user, ask exactly one well-scoped question.
triggers: ["clarify", "ambiguous", "not sure", "ask user", "need clarification"]
---
## One focused question, well-scoped

Small models tend to either plough ahead with a wrong assumption or stop
the user with a wall of bullet-pointed questions. Both behaviours are bad
defaults. The protocol is: when the task is ambiguous, ask **one**
question, scoped tightly enough that any answer unblocks the next concrete
action.

When to ask:

- The task has two or more plausible interpretations and they lead to
  different files or different commands.
- A required piece of context is missing (a file path, a target version,
  a destination, an API key) and you cannot derive it.
- You are about to do something destructive (delete files, rewrite
  history, push branches) and the intent isn't already explicit.

When **not** to ask:

- The user already gave you enough to make a sensible default. Pick the
  default, name it, and proceed. They will redirect you if it's wrong.
- You can resolve the ambiguity yourself with one cheap tool call (a
  `Read`, a `grep`, an `ls`).
- The question is "do you really want me to do what you just told me to
  do?" — that's a confirmation theatre, not a clarification.

Question shape:

```
Before I proceed: <one-sentence statement of the ambiguity>.
Option A: <concrete description, names a file or command>
Option B: <concrete description, names a file or command>
Which one?
```

Two options is usually enough. Three is the cap. If you genuinely have
five options, pick the two most likely and offer "or something else" as
an escape hatch.

Phrasing rules:

- No preamble. The user does not need "I want to make sure I understand
  correctly" — they need the question.
- Concrete artifacts (file paths, command names) over abstract phrases
  ("the file in src" → `src/lib/index.ts`).
- One question per message. If two are truly independent, ask the more
  blocking one first and save the other for after the answer.

The cost of asking is one round trip. The cost of not asking is a wrong
patch the user has to read and reject. Asking once, well, is almost
always cheaper.
