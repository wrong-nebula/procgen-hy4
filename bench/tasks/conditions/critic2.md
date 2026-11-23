## An independent critic

`python3 critique.py` asks an independent senior modeller to look at the standard renders of your module (iso,
opposite and front views, beauty and clay) against the brief. It returns a short list of **misfits**: what
reads wrong, most important first, each with a concrete change. It does not see your code.

- You may call it **at most 3 times**: after your first complete build that passes `pgm check`; again after
  you have dealt with its first list; and optionally once more before you finish.
- Treat each misfit as a light to switch off: fix them in order, re-render and look, then move on. If you
  disagree with a misfit, say why in `PLAN.md`.
- It takes about half a minute.
- It is part of this task and allowed: it is the one tool that uses the network (it sends your renders and the
  brief to the critic), and it keeps its log under `renders/`. The rule about network access does not apply to it.
- It remembers its earlier lists for your module: it will tell you which misfits are fixed and keep its advice
  consistent, so call it again after real changes rather than after small ones.
