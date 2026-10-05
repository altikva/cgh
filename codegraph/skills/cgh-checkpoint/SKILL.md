---
name: cgh-checkpoint
description: "Save the current task to cgh before a /clear or when a long session should be secured: what is done, the decisions and why, what is open, and the next step, written with the codegraph checkpoint tool so the next session can pick it up. Use when the user types /cgh-checkpoint, says they are about to clear the context, asks to save or wrap up the session, or before switching to an unrelated task."
---

# Checkpoint: write down the task before the context goes

A `/clear` or a compaction drops the conversation. cgh's lifecycle hooks
already record WHAT happened (the requests, the files edited, the commands)
from the transcript. Only you can record WHY. Do it now, in one call.

## Steps

1. Call the codegraph `checkpoint` tool:
   - `session_id`: the id from the cgh SessionStart header (`cgh session id:`).
     If there is none, use a short slug of the task.
   - `title`: the task in a few words.
   - `digest`, in this order and in plain sentences:
     1. **Goal**: what the user asked for, in their terms.
     2. **Done**: what is finished and verified (PRs, merges, commits,
        files), with numbers and paths.
     3. **Decisions**: each choice made and the reason, including what was
        ruled out and why.
     4. **Open**: what is unfinished, blocked, or waiting on someone, and on
        what.
     5. **Next step**: the single next action to resume with.
2. For each lasting fact learned this session (a gotcha, a convention, a
   standing preference) that is not already stored, call `knowledge_record`
   separately. A checkpoint is about this task; knowledge outlives it.
3. Tell the user in one line that the task is saved and that after `/clear`
   the new session will show a recap and can resume it with the codegraph
   `resume` tool.

Keep the digest under about 300 words. Specific beats complete: a path, a
PR number or an error message is worth more than a paragraph.
