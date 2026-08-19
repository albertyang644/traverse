Tree Structure

├── MAP.md  # This is the file that will map out where the important things are
├── GOALS.md  #  These are the ultimate goals of the project
├── PLANS.md  #  Canonical approved plan artifact
├── TASKS.md  #  Bite-sized implementation checklist
├── README.md  #  User-facing documentation — the authoritative feature list
├── LICENSE  #  MIT
├── main.py  #  Root launcher for the Python app
├── requirements.txt  #  Python dependencies for the app and tests
├── traverse.desktop  #  KDE menu entry (substitute INSTALL_DIR before installing)
├── src/  #  Python source package for the application
│   ├── vcs/  #  VCS-agnostic status manager, cache, worker
│   └── git/  #  The git provider (subprocess only, no Qt)
├── screenshots/  #  README images + generate.py, which re-renders them
│                 #  offscreen against a synthetic /tmp demo tree
├── docs/  #  AI generated Human Readable docs
│   ├── git-architecture.md
│   └── security-audit.md
├── tests/  #  This is where the tests for correctness lives — one directory,
│           #  run with `python3 -m pytest tests/ -q`
├── state/  #  Per-user runtime state (JSON). Written by the app, untracked,
│           #  recreated on first run. Not configuration to be shared.
└── success/  # These are the criterias for success

The workflow directories below are conventions of this repo's process, created
on demand and empty (so absent from a fresh clone): archives/ for superseded
test results, archives/deprecated/ for mistakes, contracts/ for hard
non-negotiables, logs/ for error logs, assets/ for art and icons — Traverse
itself pulls icons from the KDE theme, so it has never needed assets/.

Generate a plan and ask for human approval.  Upon Human approval:

1)  Write the plan out to PLANS.md
2)  Generate TASKS.md and break down the plan into bite sized chunks, with a checkmark in front; and every task that is completed; check it off.   
3)  Generate logs when there are errors and how you overcame them in logs/
4)  if there were tests and test results, after the tests have passed, move them to archives/ if there are deprecations put them in archives/deprecations/ 
5)  Look in the contracts/ folders to see if there are hard invariables to the project
6)  look in the success folder to see what "success" looks like and when the iteration will stop.
7)  when the project has fulfilled all success criteria, go ahead and generate relevant user documents in docs/
8)  confirm one final time that everything in TASKS.md have been met.


- If it's a program; then src/ should be created to keep source code there.

- If it's got a web interface, www/ should be generated and all interfaces should be there

- If you have art and icons and jpgs etc.. then assets/ should be created.

- If there are .css files; then assets/css should be the location for it.
