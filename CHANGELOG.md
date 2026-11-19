# Changelog

## 0.2.0 - 2026-11-19

The modelled controller now follows its own public user guide (docs/SOURCES.md, G1 to G3).

- **Priority behaviour corrected.** Version 0.1.0 modelled a KNX telegram as writing
  Relinquish_Default (assumption A4). That was an assumption, and the user guide
  contradicts it: the controller writes KNX values into one configurable slot of the
  priority array, and Relinquish_Default is set once, at the first read, and cannot be
  changed afterwards. The model now does this.
- The class "BMS command left in priority 8" is split into the three cases that are really
  different: a command above the gateway's slot masks KNX, one below it has no effect, one
  in the same slot is overwritten by KNX and its relinquish brings back the frozen first
  value. The engine reads each from the Priority_Array against the BMS command log (E3) and
  from the gateway's object priority setting (E4). 13 classes instead of 11.
- The settings-save study now reports that the save deletes every BMS command for good,
  what the BMS gets at its first renewal, and how long the gateway itself shows the frozen
  first value (new table T11).
- Promoted from assumption to documented: the export rules and limits (A1), the default COV
  increment (A3), the cancelled subscriptions and reset priority arrays on a save (A5),
  the unique device ID and the default port.
- Still assumptions, now with bench tests in docs/BENCH_TEST_PLAN.md: the default slot (A4),
  whether the reset empties the gateway's own slot (A5), what a BACnet write does on KNX
  (A16), the instance formula (A15), which objects drop out above the limit (A2).
- New open points from the guide: the "Overwritten flag" (O1) and the wording "changes by
  more than" the increment (O2, new table T12). No finding rests on either.
- Foreign-device registration listed as not modelled.

Unchanged: every recall and certainty number of the ten other classes, the COV sweeps, the
traffic table and the stale times after a save. Changed means: E1 59 % to 65 %, E2 68 % to
73 %, E3 99 % to 99.5 %, certain verdicts 1593 to 1989, still 0 wrong.

Change made during development that altered results:

- The first E3 rule called a held command "masks KNX, certain" whenever no slot above it was
  in use. With a 1 h window, a command in the gateway's own slot whose relinquish fell after
  the window end was called that way: 14 wrong certain verdicts. The rule now requires a
  used slot below the command (the gateway's slot) for "certain"; with no other slot in use
  it says "same slot, likely".

## 0.1.0 - 2026-11-19

First complete version.

- Gateway BACnet server at object and service level: export flag, Binary and Analog Value
  mapping, instance number from the group address, export limit, 16-level priority array,
  COV with per-object increment, subscription lifetime and renewal, COV settings save.
- BMS client with Who-Is binding, polling or COV subscriptions, value history and command log.
- Virtual installation with ten injected faults and a healthy class; diagnosis engine at
  four evidence levels with certainty rules; confusion matrices over 1100 installations.
- COV increment sweep, polling sweep, traffic for 150, 500 and 2000 objects, COV settings
  save study, random-walk check of the COV rule.
- README number check (`scripts/check_readme_numbers.py`) run by the tests and CI.

Changes made during development that altered results:

- The engine first checked for unreported KNX changes only after the last BMS update. With an
  8 h window, a settings save that a later renewal had healed was reported as "healthy,
  certain" in 18 of 40 runs. It now checks the whole window, and the history rule also looks
  for a common silence that ended with a renewal.
- The unreported-change test first used one increment as the tolerance. A renewal shows the
  current value without moving the server's reference value, so a working subscription can
  leave the client up to twice the increment away. The tolerance is now twice the increment
  (premise P3).
- A healthy setpoint that never changed in the window was first ranked as "increment too
  coarse". The engine now compares the increment with the nominal step of the signal (A9).
- Adding objects above the export limit could loop forever when the target's group address
  was low; the search now draws from all free addresses below the target.
