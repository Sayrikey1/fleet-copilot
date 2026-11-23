# Fleet Copilot

An agentic AI copilot for IT administrators over device-fleet telemetry. It answers
grounded fleet-health and compliance questions, surfaces trends, and **proposes
operational actions that only execute after explicit human approval** — with tenant
isolation, evidence-gated actions, and an append-only audit trail enforced in code,
not in the prompt.

Built as an explicit **LangGraph** agent (plan → act → verify → approve → audit), with a
deterministic evaluation suite that runs with **no API key**.

---

## TL;DR

```bash
just install          # uv venv + editable install (or: uv venv && uv pip install -e ".[dev]")
just seed             # build the SQLite db from the telemetry NDJSON
just demo             # watch the whole pipeline run offline — grounding, approval, guardrails
just test             # run the deterministic evaluation suite (no API key needed)
```

`just demo` runs five scripted scenarios end to end (no credentials): a grounded answer
with citations, the high-severity "answer is none" trap, an action approved and executed,
and two guardrail refusals (no-evidence and cross-tenant). It is the fastest way to
evaluate the system.

To ask live questions you need an Anthropic key (`cp .env.example .env`, add
`ANTHROPIC_API_KEY` or `CLAUDE_API_KEY`), then:

```bash
just ask "Which devices are low on disk space?" acme-001
just ask "Show me devices failing high-severity compliance checks." acme-001
```

---

## What it does (mapped to the brief)

| Capability | How |
|---|---|
| **Grounded Q&A** | Read tools run deterministic SQL; the model may only assert what the tools returned, and every device is cited `[DEVICE_ID @ timestamp]`. A grounding verifier rejects any device id not backed by evidence. |
| **Insight / trend detection** | Time-series + drift tools: battery EOL, sustained low disk, chronic RAM pressure, and compliance drift over the 30-day window (with the timestamp of each flip). |
| **Action proposals** | Four action tools (`create_upgrade_order`, `open_remediation_ticket`, `flag_device_for_replacement`, `notify_employee`), each **held for human approval**. |
| **Guardrails** | Tenant isolation + evidence-gating at the data layer, human-in-the-loop approval via LangGraph `interrupt()`, refusal on insufficient evidence, and an append-only audit log. |

---

## Agent architecture

A simple prompt wrapper would fail this task, so the orchestration is an explicit,
inspectable **LangGraph `StateGraph`** — the control flow (planning, the tool loop, the
approval pause, the grounding check) is deterministic code; only the reasoning inside each
step is the model's.

```text
            ┌──────┐     ┌───────┐     ┌───────┐     ┌────────┐     ┌─────────┐
  START ──▶ │ plan │ ──▶ │ agent │ ──▶ │ tools │ ──▶ │ verify │ ──▶ │ respond │ ──▶ END
            └──────┘     └───────┘     └───────┘     └────────┘     └─────────┘
                           ▲   │           │             │
                           │   │ (reads)   │ (action)    │ (hallucinated id)
                           └───┘           ▼             ▼
                                     ┌──────────────┐  ┌─────────┐
                                     │ approval_gate│  │ correct │
                                     │ interrupt()  │  └─────────┘
                                     └──────────────┘
```

- **plan** — the model decomposes the question into intended tools + tenant (the brief's
  "Planning"). Best-effort; the agent proceeds if planning is unavailable.
- **agent** — `ChatAnthropic` (temp 0) bound with the read + action tool schemas; emits
  tool calls. A bounded loop (`max_tool_rounds`).
- **tools** — a custom dispatcher that **injects `company_id` from the session** and ignores
  any company argument the model supplies. Read tools execute and attach evidence; action
  tools are **held** (a `ProposedAction`) or **refused** (a guardrail violation), never run.
- **approval_gate** — `interrupt()` pauses the graph for a human decision; on approve, the
  action executes and is audited; on deny, it is recorded and dropped.
- **verify** — every device id in the draft answer must appear in gathered evidence; if not,
  a single **correct** loop asks the model to re-answer using only cited devices.
- **respond** — returns the grounded, cited answer.

The tenant principal and the database handle live in node closures (`ctx`), so they never
enter the checkpointed state, which stays JSON-friendly for the `MemorySaver` checkpointer
that powers the interrupt/resume.

Source: [`src/fleet_copilot/graph.py`](src/fleet_copilot/graph.py),
[`runner.py`](src/fleet_copilot/runner.py).

---

## Tool catalog

Tools are decorator-registered with read/write typing and **import-time invariants**
(a tool mis-declared as a silent write fails at registration):
[`src/fleet_copilot/registry.py`](src/fleet_copilot/registry.py).

### Read tools (grounding) — [`tools/reads.py`](src/fleet_copilot/tools/reads.py)

Every call is tenant-scoped via `ctx`; every result row carries `snapshot_id` + `collected_at`.

| Tool | Purpose |
|---|---|
| `get_fleet_summary` | Device count + latest-known state per device |
| `find_devices_by_disk` | Low root-volume free space (mean / latest / sustained) |
| `find_devices_by_memory` | High mean RAM utilisation |
| `find_devices_by_battery` | Batteries near EOL (cycle count or service-flagged); excludes battery-less devices |
| `find_devices_by_os` | OS-family-scoped version floor (never cross-compares families) |
| `get_compliance` | Compliance by check / severity / status (latest per device) |
| `detect_compliance_drift` | Status that flipped over the window, with the flip timestamp |
| `get_device_history` | A device's time-series for one metric |
| `list_software` | Installed software across the fleet |

### Action tools (gated) — [`tools/actions.py`](src/fleet_copilot/tools/actions.py)

Each requires approval, validates the target belongs to the tenant, and requires supporting
evidence before it can even be *proposed*.

| Tool | Evidence it requires |
|---|---|
| `create_upgrade_order(device_id, component, spec)` | the device is actually constrained on that component |
| `open_remediation_ticket(device_id, check_id, note)` | the device is actually failing that check |
| `flag_device_for_replacement(device_id, reason)` | at least one EOL signal (battery / disk / RAM) |
| `notify_employee(employee_id, message)` | the employee belongs to the tenant |

---

## Grounding strategy

The design choice that covers "traceable", "cited evidence", and "deterministic execution"
at once: **the model plans and narrates; SQL computes.**

1. Telemetry is flattened into an indexed SQLite db ([`ingest.py`](src/fleet_copilot/ingest.py)),
   with `company_id`/`device_id`/`collected_at` denormalized onto every row.
2. Read tools return typed `EvidenceRow`s, each pinned to a snapshot id + timestamp.
3. The system prompt forbids asserting anything a tool didn't return, and requires
   `[DEVICE_ID @ timestamp]` citations.
4. The **verify** node mechanically checks that every device id in the answer is backed by
   evidence; a hallucinated id triggers a correction pass
   ([`grounding.py`](src/fleet_copilot/grounding.py)).

Because the numbers come from SQL (not the model), answers are reproducible and auditable.

---

## Guardrails

All enforced in code at the tool/data layer — not via prompt instructions.

- **Tenant isolation.** Every data-access method requires `company_id` as a mandatory
  argument ([`db.py`](src/fleet_copilot/db.py)); the tenant is injected from the session
  principal ([`context.py`](src/fleet_copilot/context.py)), never from the model. A
  cross-tenant action is refused before it reaches the approval gate.
  *(This is a deliberate fix for a real pattern seen in prior work, where an optional
  `org_id` defaulted to `None` and silently disabled tenant filtering.)*
- **Human approval for every action.** State-changing tools never auto-run; they pause the
  graph via `interrupt()` and execute only on an explicit approve.
- **Refusal on insufficient evidence.** Actions validate real telemetry first — e.g.
  "flag every device failing a high-severity check" flags nothing, because no device fails
  one (the only high-severity check always passes).
- **Append-only audit log.** Every read, proposal, approval, denial, execution and refusal
  is written to the `audit_log` table *and* a JSONL mirror
  ([`audit.py`](src/fleet_copilot/audit.py)). View it with `just audit acme-001`.
- **Fail-fast registration.** An action tool that doesn't declare `requires_approval=True`
  raises at import.

---

## Running evaluations

```bash
just test           # deterministic suite — no API key, no network
just live-eval      # optional: the 3 example questions against the real model (needs key)
```

The suite is two-tier, built for determinism (a scripted fake model stands in for the LLM,
mirroring the approach in a prior codebase):

| File | What it locks down |
|---|---|
| [`test_tools_golden.py`](tests/test_tools_golden.py) | **Tier 1:** read tools return the exact, hand-verified device sets per tenant — no LLM. Correctness can't hide behind the model. |
| [`test_graph_trajectory.py`](tests/test_graph_trajectory.py) | **Tier 2:** the graph calls the *right* tool and grounds its answer. |
| [`test_guardrails.py`](tests/test_guardrails.py) | tenant isolation, approval-gating (held / denied / approved), evidence-gated refusals. |
| [`test_grounding.py`](tests/test_grounding.py) | hallucinated ids are caught and corrected. |
| [`test_audit.py`](tests/test_audit.py) | every interaction is audited, append-only, tenant-scoped. |

Golden answers live in [`tests/golden/golden_qa.json`](tests/golden/golden_qa.json) and were
derived by profiling all 750 snapshots. Key verified facts (the eval baseline):

| Question | Answer |
|---|---|
| Devices failing **high-severity** checks | **0** (trap: `disk_encryption` always passes) |
| Devices older than **macOS 15** | **10** (macOS-only; the 240 Windows snapshots excluded) |
| **Low disk** (sustained) | 6: `MT7PJB7N5LRE` `RLD7WV7J82SH` `JRZSGXVMKE6M` `DARFCP0BQM2G` `E4V0SB5I0MYU` `8NM23J95R5I6` |
| **RAM constrained** | 4: `M4XVHUV1MEPZ` `I3JLKILKQTAC` `JRZSGXVMKE6M` `CUXW0BQSV1B7` |
| **Battery near EOL** | 3: `8NM23J95R5I6` `7VP16KHV88LM` `M4XVHUV1MEPZ` |
| **screen_lock drift** (pass→fail) | 10 devices, each with a flip timestamp |

Thresholds for "low"/"constrained" live in [`settings.py`](src/fleet_copilot/settings.py)
and are chosen at natural gaps in the data, so the golden sets are stable.

---

## Data model

~750 daily snapshots, 25 devices, 3 tenants (acme-001: 10 devices, globex-002: 8,
initech-003: 7), over 2026-05-14 → 2026-06-12; 510 macOS / 240 Windows snapshots. Known
data subtleties handled explicitly: **battery is dropped per-snapshot** (100 snapshots lack
it — never treated as 0), and OS version comparisons are family-scoped.

Schema: `snapshots` (flattened scalars + `raw_json`), `compliance_results`,
`installed_software`, `audit_log` — see [`ingest.py`](src/fleet_copilot/ingest.py).

---

## Key design decisions & trade-offs

- **LangGraph over a hand-rolled loop.** The brief prefers it, and `interrupt()` is a clean,
  native human-in-the-loop primitive for the approval gate. Trade-off: one more dependency
  and a checkpointer; mitigated by keeping non-serializable objects out of state.
- **SQLite + deterministic tools over stuffing JSON into the prompt.** Buys grounding,
  reproducibility, and citation (row → snapshot) in a single move, and keeps the model off
  the critical path for correctness.
- **Explicit, named read tools over one generic query tool.** Cleaner trajectories to assert
  in eval, and far less room for the model to hallucinate a query.
- **Tenant + evidence enforced at the data/tool layer, not the prompt.** Prompt-only
  isolation leaks; a mandatory `company_id` argument cannot be forgotten.
- **Two-tier, model-free eval.** Tier 1 tests correctness without any LLM; Tier 2 tests the
  graph with a scripted fake. The whole suite runs in CI with no key — the live tier is
  opt-in.
- **Thresholds as config, chosen at data gaps.** Keeps golden answers deterministic and makes
  "what counts as low" a single, reviewable knob.

---

## Project layout

```text
src/fleet_copilot/
  settings.py     # one memoized config object; thresholds = single source of truth
  ingest.py       # NDJSON -> SQLite (idempotent)
  db.py           # tenant-scoped query layer (company_id mandatory)
  context.py      # SessionContext — the tenant principal
  registry.py     # @read_tool / @action_tool + import-time invariants
  tools/          # reads.py (grounding), actions.py (the 4 gated actions)
  grounding.py    # EvidenceRow + the citation verifier
  graph.py        # the LangGraph StateGraph
  runner.py       # invoke + interrupt/resume loop
  audit.py        # append-only audit sink (SQLite + JSONL)
  llm.py          # ChatAnthropic factory + the model interface
  prompts.py      # composed system prompts
  cli.py          # typer CLI: seed / ask / demo / audit-log
  demo.py         # offline scripted end-to-end demo
tests/            # the two-tier eval suite + fixtures/fake_llm.py
data/             # device-telemetry-dataset.ndjson
```

## Setup notes

- Requires Python 3.12 and [`uv`](https://docs.astral.sh/uv/). `just install` creates the
  venv and installs the package; without `just`, run `uv venv && uv pip install -e ".[dev]"`.
- The deterministic suite and the `demo` need **no API key**. Only live `ask` does.
- Runtime artifacts (`fleet.db`, `audit_log.jsonl`) are created in the working directory and
  are git-ignored; rebuild `fleet.db` any time with `just seed`.
