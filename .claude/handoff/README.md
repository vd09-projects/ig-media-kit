# Handoff Directory

This directory holds the audit trail of every meaningful planning, building, and reviewing event in this project. Skald (the orchestrator skill) maintains it. Humans read it.

If you joined the team and someone said "go read the handoff dir to get up to speed" — this README explains how.

**One thing to know up front:** producer skills (mimir, sindri, multi-perspective-review, etc.) don't know about this directory. They produce natural markdown output. Skald reads their output, classifies it by the title line (`# Architecture: ...`, `# Task plan: ...`, `# Build summary: ...`, `# Review findings: ...`), wraps it with metadata, and writes the canonical file here. The on-disk format is skald's concern; producer skills stay focused on their craft.

---

## How to navigate

- **"What's in flight right now?"** → `INDEX.md`. One row per scope, sorted by most recently updated. Status column tells you each scope's state.
- **"What happened recently?"** → `LOG.md`. Append-only chronology, one row per skill invocation, newest at bottom.
- **"The full story of one piece of work?"** → `{slug}/_thread.md`. Narrative log for one scope.
- **"The actual plan / build / review artifact?"** → the canonical file in the scope dir:
  - `{slug}/planner-architecture.md` — architecture-level plan
  - `{slug}/planner-task.md` — task-level breakdown
  - `{slug}/implementation-build.md` — what was built
  - `{slug}/review-findings.md` — review findings
- **"How this plan evolved?"** → `{slug}/_history/` — every prior version as `{canonical}-v{N}.md`. Files are never deleted.

---

## Directory layout

```
.claude/handoff/
├── README.md              # this file
├── INDEX.md               # per-scope status table
├── LOG.md                 # append-only chronology
├── {slug}/
│   ├── _thread.md         # narrative log for this scope
│   ├── planner-architecture.md
│   ├── planner-task.md
│   ├── implementation-build.md
│   ├── review-findings.md
│   └── _history/
│       └── {canonical}-v{N}.md
└── ...
```
