# Building new kits

A kit module is a Python **builder** that writes a geometry-graph document. `pgm` checks and renders that
document. This guide goes from an idea to a module that passes every gate. Agents also read
[AGENTS.md](AGENTS.md) for the hard rules.

## 1. Pick the model

New kits are hard: the model has to plan an object, compute where every part meets, write a few hundred lines
of geometry and judge its own renders. **Use the strongest vision model you can for a new kit.** Cheaper models
are good at refining or varying a kit that already works.

| Job | Model | Why |
|---|---|---|
| A new kind of object, first build | The strongest frontier model you have, with vision and high reasoning effort | It plans the datum table, mates the parts and fixes what it sees in the renders |
| Variants, refinement, the evolution loop | A cheap model (e.g. Hy4 Flash) starting from a working builder | Small, function-sized edits; the gates catch mistakes |
| Offline work with local models | Use [procgen-kit](https://github.com/wrong-nebula/procgen-kit), the any-model version of this repo | It adds Ollama, LM Studio, llama.cpp, vLLM and MLX |

What the benchmark runs behind this repo found (blind-scored renders, 1–5):

- **Best single builds:** a frontier model at high reasoning effort (gpt-6.1-terra, high) with a
  *constructive* brief scored 4.25–4.75 for about $0.20–0.35 per kit.
- **The brief mattered most**, then reasoning effort. Scaffolds (plans, checklists, worked examples,
  best-of-N) mostly did not help.
- **Escalating** a weak build once to a stronger model (gpt-6.1-sol) improved about 4 in 11.
- **Families:** building several sibling modules in one session was about 4× cheaper per module and more coherent.
- **Hy4 Flash** with the evolution loop cost $0.04–0.06 per kit and won 2 of 4 blind comparisons against
  frontier builds of the same brief.
- **Vision:** text-only models cannot see their renders and iterate blind on `pgm`'s numbers.

## 2. Write a constructive brief

Make a folder `bench/tasks/<kit>/` with two files. Copy an existing task (`bench/tasks/door-knocker/` is short)
and change it.

`brief.md` describes the object in four sections. This structure is what made briefs work:

```markdown
## The object: <one line>
What it is: the parts, as bullets, each named.

## How it is built
What sits on what, what is fixed and what moves with each control.

## Proportions (derive them with `G.let`)
Sizes relative to the governing control, with real ranges.

## Must have
The parts that must be there, and the material slots.

Frame and controls:
- Z-up, metres. The front faces -y. Where z = 0 and the origin are.
- Controls: `name` default in [min, max] (what it does).
- Ports: name, position, facing, type.
```

`task.json` holds the contract the harness checks at the defaults and at every range end:

```json
{
  "id": "door-knocker", "title": "Cast door knocker", "module": "door-knocker",
  "contract": {
    "controls": {"scale": [1, 0.7, 1.4], "lift": [0, 0, 1]},
    "ports": {"mount": {"type": "fitting", "origin": [0, 0, 0], "normal": [0, -1, 0], "dims": {"width": 0.16}}},
    "envelope": {"y": ["-0.4", "0.02"]},
    "min_materials": 2
  },
  "renders": [{"name": "iso-beauty", "view": "iso", "pass": "beauty", "size": "1280x960"}]
}
```

## 3. Build it

### With your coding agent (recommended for new kits)

Open the repository in Claude Code, Codex, opencode, Cursor or any agent, choose its strongest model, and ask:

> Read AGENTS.md and bench/tasks/<kit>/brief.md. Build `examples/<kit>/<kit>.py` starting from
> `examples/starter/bollard.py`. Write the datum table first. Run `verify_round.py` after every change and
> look at the render each time. Stop when it prints `ALL GATES PASS` and the renders read as the object in
> the brief, from the front and from the back.

### With the built-in loop (any model, unattended)

`mini_mode.py` runs a tool-calling session in a sandbox copy of the repository. The model writes
`recipes/build_bench_kit.py`, previews it and fixes it. Any model name works:

```bash
python3 bench/harness/mini_mode.py run door-knocker --model anthropic/claude-opus-5.5 --max-calls 60
python3 bench/harness/mini_mode.py run door-knocker --model hy4-flash-xhigh --max-calls 60
```

The builder (`build.py`), the model's `NOTES.md` and the generated kit (`out/`) land in
`bench/runs/<date>/<task>/<model>--LM-r1/`; the sandbox stays in `/tmp/pgbench/` for inspection. To evolve a kit with a cheap model,
`kit_evolve.py` seeds several builders, edits one function at a time and tunes proportions:

```bash
python3 bench/harness/kit_evolve.py evolve door-knocker --model hy4-flash-xhigh --out /tmp/evolve-door-knocker
```

Model names are OpenRouter ids (`anthropic/claude-opus-5.5`, `openai/gpt-6.1-sol`, …) or the short names in
`bench/harness/models.json`. The key goes in `PGBENCH_OPENROUTER_KEY` or `~/.config/pgbench/openrouter.key`.

## 4. Check it

```bash
(cd procgen && python3 tools/kit_harness/verify_round.py --script ../examples/<kit>/<kit>.py \
  --doc ../examples/<kit>/out/<kit>/<kit>.json --render /tmp/<kit>.png --stamp /tmp/<kit>.ok)
```

`ALL GATES PASS` means: the build runs, every mesh is closed and manifold with no self-intersections, every
control works at its minimum and maximum, every part touches the rest within 3 mm, and MuJoCo finds no
clipping. Then look:

```bash
P=procgen/build-release/pgm; D=examples/<kit>/out/<kit>/<kit>.json
$P render $D --out /tmp/a.png --view iso                  # also: opposite, front, side, top
$P render $D --out /tmp/b.png --view opposite --pass clay  # the back must be finished too
$P render $D --out /tmp/c.png --pass parts                 # one colour per part
python3 procgen/tools/kit_harness/diag.py $D /tmp/d.png $P # axes, ports and a 1.75 m figure for scale
```

## 5. When a gate fails

| Gate | Usual cause | Fix |
|---|---|---|
| `PGM_CHECK` self-intersections | Two solids of one material overlap | Butt them (gap 0.0005 m) or give one another material |
| `PGM_AUDIT` detached | A part floats more than 3 mm away | Place it from the datum table, not by eye |
| `PGM_SWEEP` | The model breaks at a control's minimum or maximum | Derive sizes from the control, or narrow its range |
| `MUJOCO_AUDIT` clips | A shaft through a solid hub | Bore it: `r_bore = r_shaft + 0.0018` (`bored_collar`, `bored_nut`) |
| Details missing | Rivets or lettering added before `subdivide` or `chamfer` | Add surface details last |

## 6. Keep it

Commit the builder and its generated document under `examples/<kit>/`. Add a render to the README if you are
proud of it.
