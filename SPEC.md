# Persistent Learning Companion — SPEC

## North star

> Do not optimize how much the AI says.
> Optimize whether the AI knows **the learner’s next most needed action**.

Companionship is not “being present while someone studies.”
It is a **persistent external cognition system** that keeps the learner inside an
**effective learning loop**:

`Attempt → Feedback → Update → Next Attempt`

and repairs the loop at the lowest cost when it is about to break.

## Product definition

**Now (interaction layer):** Local Voice Character Companion  
`mic → VAD → STT → LLM → TTS → character`

**Next (learning loop layer):** Persistent Learning Companion  
A desktop partner that continuously understands:

- what you are trying to learn
- where you are now
- where you are stuck
- when to intervene
- what to push next

## Explicit Learning State

Stored in `pipeline/companion/learning_state.yaml` (editable in UI):

| Field | Meaning |
|-------|---------|
| `active` | Learning session on/off |
| `goal` | What “done” looks like |
| `current_task` | What we are doing now |
| `current_understanding` | Best estimate (not self-report alone) |
| `uncertainty` | What is unclear |
| `blockers` | What is blocking progress |
| `last_attempt` | Last concrete attempt |
| `evidence` | Observable evidence only |
| `next_action` | Single minimal next action |
| `next_checkpoint` | How we will verify next |
| `intervention` | `answer \| probe \| verify \| redirect \| hint \| silence` |

**Evidence rule:** “I understand” is not evidence. Explanation, quiz answer,
prediction, or produced work is.

## Intervention policy

The model must choose **one** intervention per turn:

- **silence** — in loop, no ask → barely speak
- **probe** — clarify goal / current understanding
- **verify** — minimal check question
- **redirect** — pull back to goal/task
- **hint** — smallest unblock
- **answer** — tiny knowledge patch, then return to practice

## MVP scope (this iteration)

In scope:

1. Learning State model + persistence + prompt injection
2. Auto-activate when user says they want to learn / 搞懂…
3. Post-turn state update from dialogue (async, JSON)
4. UI card to view/edit Learning State
5. Persona `learn` mode + intervention rules

Out of scope (later):

- Screen / Cursor / browser observation
- Screenshot VL
- Full curriculum / XP / course player
- Cloud LLM requirement

## Success check

A successful learn turn looks like:

1. User: “I want to get useEffect this hour.”
2. State: goal + next_checkpoint set
3. Companion asks for current understanding or a tiny verify
4. User answers
5. State `evidence` / `understanding` / `next_action` update
6. Next turn continues from state — not a fresh ChatGPT answer

## Non-goals for voice layer

Do not prioritize more Whisper/TTS polish until the learning state loop works.
Ears/mouth/body exist; **the missing piece is state in the brain.**
