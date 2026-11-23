## Shared memory of pitfalls

`memory/` holds lessons other modellers recorded while building kits with this toolkit: pitfalls and their fixes.

- **At the start**, read `memory/INDEX.md` (one line per lesson).
- **When something fails**, `pgm` adds matching lessons to its output as `hints`. You can also ask:
  `python3 memory/lessons.py "<error text or topic>"`. Look there before experimenting.
- **Before you write `NOTES.md`**, record up to 3 new lessons for problems you solved that no lesson covers,
  general enough to help on another object:
  `python3 memory/lessons.py --add --title "..." --match "<regex for the error text>" --tags a,b --cause "..." --fix "..."`
  If a lesson helped, run `python3 memory/lessons.py --hit <id>`.

You may create and edit files under `memory/` as well as the files listed under the rules below.
