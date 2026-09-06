Read `design/method.md` — it names what your seat reads — then `design/intention.md`. If `notes/NOW.md` exists, read it before doing anything else.

For the development roadmap, inspect waiting notes at task resumptions and module handoffs with `python3 design/bridge.py --comments notes/comments.jsonl --list --waiting --json` when that file exists. Treat notes as design input to consider, not automatic execution authority. Update the dated private `notes/roadmap-status.json` atomically after meaningful progress; keep completed target identities and historical comments. See `tools/roadmap.md`.
