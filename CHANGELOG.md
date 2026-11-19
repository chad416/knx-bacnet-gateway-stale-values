# Changelog

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
