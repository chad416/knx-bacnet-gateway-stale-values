# Engineering dossier: stale, missing and moved values behind a KNX-to-BACnet gateway

Simulated study. No hardware and no recording from a real installation was used. All result
numbers are in `results/summary.json` and `results/tables.md` (tables T1 to T10), produced by
`python scripts/run_all.py`. Source ids (B1 to B8) refer to `docs/SOURCES.md`. No
conformance to the BACnet standard is claimed. The structure follows Wasson, *System
Engineering Analysis, Design, and Development*.

## 1. Need and operational concept

**Need.** A building management system (BMS) shows the values of a KNX installation. It
reads them from a KNX logic controller that runs a BACnet/IP server. The operator reports
"this value is old", "this point is in fault", "the readings jumped to other rooms". The
case reaches expert support after two tiers could not close it. The support engineer
needs to know which causes the available evidence proves, which it cannot, and which one
cheap piece of evidence to ask for next. The commissioning engineer needs a rule for the
COV increment and for polling or renewal intervals.

**Question.** Which causes can be told apart with certainty, from which evidence, and what
does a COV setting cost in missed changes against network traffic?

**Scenarios.**

| Id | Scenario | Evidence available |
|---|---|---|
| OS-1 | Customer sends screenshots or an export of the BMS trend log | E1: value history, fault flags, BMS command log |
| OS-2 | Engineer with remote access runs one ReadProperty from a BACnet tool | E2: E1 + direct read of the complaint point |
| OS-3 | Engineer also runs Who-Is, reads the object list and the target's Priority_Array, and asks the BMS integrator for the polling or subscription settings | E3 |
| OS-4 | Engineer gets the gateway's export configuration and a KNX group monitor recording of the period | E4 |
| OS-5 | Commissioning: choose COV increments, polling or renewal intervals for 150, 500 or 2000 objects | trade study, section 6.2 |

**System of interest.** A diagnosis engine plus a model of the chain KNX group object ->
gateway BACnet server -> BACnet client. The model is the test bench: it produces evidence
of a virtual installation with one known injected fault.

## 2. Modes and states

**Exported object** (`bacgw/server.py`):

```
 configured --export flag on, type binary or numeric, within limit--> visible (Analog / Binary Value)
 visible --flag removed / limit exceeded / group address changed--> absent
          (its subscriptions are dropped silently; reads answer OBJECT/UNKNOWN_OBJECT)
 configured --type date, time or text--> never visible
```

**Present_Value** = first non-NULL slot of the 16-slot priority array, else
Relinquish_Default (B2, B4). KNX telegrams write Relinquish_Default (assumption A4).

**COV subscription** (per client, process id, object):

```
 none --SubscribeCOV(lifetime L)--> active, immediate notification with the current value
 active --|PV - PV at last report| >= increment (analog) / any change (binary)--> notify, stay active
 active --SubscribeCOV again--> active, lifetime restarts, immediate notification (B3)
 active --L > 0 and L elapsed--> none (silently)
 active --object becomes absent--> none (silently)
 active --COV settings saved--> none (silently), and all priority arrays reset (A5)
```

**Client binding.** Who-Is at start; any later I-Am for the same device instance replaces
the address (last one wins, B7, A11).

**Diagnosis session.** E1 -> ranked hypotheses -> ask for the evidence that separates the
top set -> next level -> report with the evidence lines.

## 3. Requirements

| Id | Requirement | Source | Verification |
|---|---|---|---|
| R-01 | Only objects with the export flag are visible | B1 | test `test_export_flag_required` |
| R-02 | Binary types -> Binary Value, numeric types -> Analog Value, other types not exported | B1, research notes | test `test_binary_becomes_bv_numeric_becomes_av_others_not_exported` |
| R-03 | Instance = X * 2048 + Y * 256 + Z for group address X/Y/Z (0/0/1 -> 1, 1/1/1 -> 2305) | B1 | tests `test_instance_is_group_address...`, `test_every_instance_maps_back` |
| R-04 | Small variant exports at most 150 objects | A1 | test `test_export_limit_keeps_lowest_instances` |
| R-05 | Present_Value from the highest-priority non-NULL slot of 16, else Relinquish_Default; NULL write relinquishes | B2, B4 | test `test_priority_array_highest_non_null_wins...` |
| R-06 | Analog COV report when the change since the last report is >= the increment; binary on any change | B2 | tests `test_cov_increment_rule_analog`, `test_binary_reports_every_change` |
| R-07 | Default COV increment 1 | A3 | test `test_default_cov_increment_is_one` |
| R-08 | Subscription lifetime in s; 0 never expires; renewal refreshes and notifies | B3, B5 | tests `test_subscription_lifetime...`, `test_renewal_refreshes...` |
| R-09 | Unknown object: OBJECT/UNKNOWN_OBJECT; unknown property: PROPERTY/UNKNOWN_PROPERTY; full table: RESOURCES/NO_SPACE_TO_ADD_LIST_ELEMENT | B2, B3, B5 | tests `test_read_errors`, `test_subscribe_unknown_object_and_no_space` |
| R-10 | Saving COV settings cancels all subscriptions and resets all priority arrays | A5 | test `test_saving_cov_settings_cancels...` |
| R-11 | Device instance 0 to 4 194 302 | B6 | test `test_device_instance_range` |
| R-12 | The engine uses only the evidence of its level | need | interface of `diagnose(ev, level)`, section 4 |
| R-13 | A verdict marked "certain" must not be wrong | need | evaluation: 0 wrong of 1593 (T1) |
| R-14 | A healthy installation must not be accused at full evidence | need | 100 of 100 healthy runs right and certain at E4 (T1) |
| R-15 | Every hypothesis names the evidence it rests on | need | `report()`; test `test_certain_verdicts_name_their_evidence` |
| R-16 | The COV rule must reproduce the random-walk report rate a^2 / sigma^2 | analysis | T9: within 3.1 % at 0.1, 13.2 % worst |

## 4. Architecture and interfaces

```
 bacgw/knx.py        group addresses, instance formula, datapoint types, value generators (R-03)
 bacgw/server.py     BACnet server: export, objects, priority array, COV, services   (R-01..R-11)
 bacgw/client.py     BMS: binding, polling, subscriptions, value history, command log
 bacgw/sim.py        event loop, random installation, fault injection, Evidence record
 bacgw/diagnose.py   features, rules per level, certainty, report text              (R-12..R-15)
 bacgw/evaluate.py   confusion matrices
 bacgw/covstudy.py   increment sweep, polling sweep, traffic, save study, random-walk check
```

**Interface between simulator and engine: the `Evidence` record.** E1: per point the list
of (time, value) the BMS stored, a fault flag with time, and the BMS command log. E2: the
answer to one ReadProperty of the complaint point's Present_Value. E3: all I-Am answers to
a Who-Is for the device instance, the object list (with the names of objects that were not
there at commissioning), the target's Priority_Array, the model name, and the client's
configuration and subscription records. E4: the gateway's export configuration (name,
group address, datapoint type, flag, increment, limit) and the KNX group monitor of the
window. The `truth` field holds the injected fault and is never read by the engine.

**Certainty premises.** A verdict is "certain" only if the evidence shows something that,
under these premises, no other class of the catalogue produces:

- P1 One fault at a time, and the BMS is the only client that writes to the gateway.
- P2 The client accepts a notification by device instance and object, whatever the address.
- P3 A working subscription reports every change of at least the increment (R-06). Because a
  renewal shows the current value without moving the server's reference, the client value
  then stays within twice the increment of the KNX value (any change for binary).
- P4 The gateway is not restarted inside the window. A restart would also cancel the
  subscriptions; the class "subscriptions lost by a settings save" is really "lost at the
  gateway", and the root cause needs one question to the customer.

## 5. Performance budgets and margins

**Traffic budget of one gateway** (T5, object mix A10, unconfirmed / confirmed messages per
second):

| Objects | COV fine (0.1 K, 10 W) | COV default 1 | Polling 60 s, 1 ReadProperty each | Polling 60 s, RPM of 20 |
|---|---|---|---|---|
| 150 | 0.26 / 0.52 | 0.18 / 0.37 | 5.00 | 0.27 |
| 500 | 0.87 / 1.74 | 0.61 / 1.22 | 16.67 | 0.83 |
| 2000 | 3.49 / 6.98 | 2.44 / 4.88 | 66.67 | 3.33 |

Fine increments cost 43 % more notifications than the default (6.28 against 4.39 per object
per hour) because switches and power values, which the increment does not change, make up
most of the traffic. Polling with one ReadProperty per object costs 19 times more than fine
COV at 2000 objects. Renewals add 2 messages per object per renewal interval: 1.0 message/s
for 150 objects at 300 s, 13.3 for 2000 objects at 300 s, 4.4 at 900 s.

**Staleness budget of a temperature** (T4, T6). With COV the error is bounded by the
increment (0.19 K at 0.2 K, 0.99 K at 1 K) but the time is not: the value can stay wrong
for most of a day (23.48 h at 1 K). With polling the time is bounded by the interval
(longest stale 59 s at 60 s) but the error is not. A renewal every 1800 s bounds the
staleness under COV and reduces the missed share at 1 K from 96 % to 60 %.

## 6. Trade studies

### 6.1 Which evidence to ask for

| Alt. | Evidence | Cost to the customer |
|---|---|---|
| E1 | BMS trend export | none |
| E2 | + one ReadProperty | a BACnet tool on the network, 1 minute |
| E3 | + Who-Is, object list, Priority_Array, client settings | the same tool, and a question to the BMS integrator |
| E4 | + export configuration, KNX group monitor of the period | access to the gateway configuration and a running KNX recording |

**Decision rule.** No wrong certain verdict is acceptable. Among the alternatives that meet
this, prefer the one with the most classes right and certain in at least 90 % of the runs,
then the lower cost.

**Result** (T1, 100 runs per class, 11 classes):

| Alt. | Mean right first | Certain right : wrong | Classes certain in >= 90 % |
|---|---|---|---|
| E1 | 59 % | 0 : 0 | 0 |
| E2 | 68 % | 0 : 0 | 0 |
| E3 | 99 % | 494 : 0 | 5 (lifetime expired, group address changed, priority 8 left, duplicate device, polling too slow) |
| E4 | 100 % | 1099 : 0 | 11 |

**Decision.** First remote step: E3, not E2. The single direct read only splits "stale"
from "missing" (68 % against 59 %); the Who-Is, the object list and the client's settings
make the right class first in 99 % of the runs and prove five classes. E4 is needed only to
prove the remaining five (export flag, type, limit, increment, settings save), and they are
already ranked first at E3 in 99 % to 100 % of the runs.

### 6.2 How the client should get values

| Alt. | Setting | Temperature missed / p99 error | Switch missed | Messages per object per hour |
|---|---|---|---|---|
| a | COV, increment = sensor step (0.1 K, 10 W) | 0 % / 0.00 K | 0 % | 6.28 (mix) |
| b | COV, default 1 | 96 % / 0.96 K | 0 % | 4.39 (mix) |
| c | COV, 0.2 K | 67 % / 0.19 K | 0 % | 1.39 for a temperature |
| d | Polling 60 s, one ReadProperty | 0 % / 0.11 K | 4 % | 120 (60 requests + 60 answers) |
| e | Polling 300 s | 10 % / 0.13 K | 22 % | 24 |

**Decision rule.** Lose no switch change, keep a temperature within 0.2 K in 99 % of the
time, then lowest traffic. **Decision: (a)**, renewed every 300 to 900 s (section 6.3).
(b) fails the temperature, (d) and (e) fail the switch, (c) passes but saves only 4.1
notifications per hour on a temperature while it hides two thirds of its changes. The
non-obvious part: the increment that matters is the sensor's own send step; anything finer
adds no traffic, anything coarser loses changes at once.

### 6.3 Renewal interval against a settings save

T8: the time values stay stale after a COV settings save equals the time to the next
renewal (mean 2.0 min at 300 s, 12.8 min at 1800 s, 75.9 min at 14400 s; for good without
renewal). A command the BMS holds is reset at the save, and the BMS learns this only at the
renewal (3.1, 16.6, 90.1 min on average). Choosing 300 to 900 s costs 1.0 to 0.33 messages
per second for 150 objects, and 13.3 to 4.4 for 2000.

## 7. Model and validation

### 7.1 Assumptions

| Id | Assumption | Why | Carries | Effect if wrong / sensitivity |
|---|---|---|---|---|
| A1 | Small variant exports at most 150 objects, larger one has no hard limit | research notes on an unnamed product guide; B1 confirms only the 2000 recommendation | class OVER_LIMIT, R-04 | the E4 rule uses "flagged and exportable count > limit", so any limit value works if it is known |
| A2 | Above the limit the lowest instances are kept | not documented | which object drops out | diagnosis rule independent of it |
| A3 | Default COV increment 1 | research notes; B1 gives no default | finding 4 | if the default were 0.1, COV_INC_COARSE would only come from a deliberate setting |
| A4 | KNX telegrams write Relinquish_Default; a held BACnet command masks them; the gateway writes the resolved value to KNX and hears its own telegram | not documented anywhere I found | class PRIORITY_STUCK | if KNX overwrote Present_Value directly, this class would not exist; other classes unchanged |
| A5 | Saving COV settings cancels all subscriptions and resets all priority arrays | research notes | finding 5, class COV_SUB_LOST_SAVE | without it the class vanishes; a gateway restart would still produce the same signature |
| A6 | A renewal triggers a notification with the current value | B3 (refresh sets a send flag), B5 (initial notification) | T4 last column | without it, renewal would not bound staleness under a coarse increment |
| A7 | 4 h window, complaint at its end | typical "since this morning" complaint | T1 | 1 h and 8 h: mean right-first 60 to 64 % at E1, 99 to 100 % at E3, 0 wrong certain (T10) |
| A8 | Polling up to 300 s is acceptable | display of room values | defines POLL_TOO_SLOW | a different limit moves the class boundary, not the method |
| A9 | Nominal KNX step when the window shows none: 0.1 K, 0.5 K setpoint, 10 W | sensor send steps of A12 | healthy verdict of a value that never changed | a coarser nominal step would let a coarse increment pass as healthy |
| A10 | Object mix 35 % temperature-like, 10 % power, 35 % binary, 20 % quiet (1 change per hour) | no measured mix available | T5 traffic | traffic scales linearly with each share |
| A11 | Last I-Am wins also on BACnet/IP | B7 states it for MS/TP | class DUP_DEVICE values | the E3 proof (two I-Am answers) does not depend on it |
| A12 | Signals: room temperature with 0.25 K standard deviation, 2 h correlation time, trend up to 0.3 K/h, sent on 0.1 K change; power steps of 400 W every 180 s, meter sends on 10 W change every 10 s; switch changes every 900 s, 30 % short pulses of 30 to 120 s | plausible room and distribution values; no measured data | findings 4 and 5 | T10: COV 0.2 K against polling flips from 3600 s to never (half the movement) or 900 s (double); a 0.2 K sensor step makes 0.2 K the right increment |
| A13 | The KNX group monitor covers the whole window | a logger was running | E4 | without it, a healed settings save cannot be proven |
| A14 | 65 % of healthy clients use COV; lifetime 0 without renewal, 3600 s renewed every 1800 s, or 28800 s renewed every 14400 s | common client behaviours | mix of signatures | listed in `summary.json`, `client_mode` |

### 7.2 How we know the model is right

| Check | Reference | Result |
|---|---|---|
| Instance formula | B1 examples 0/0/1 -> 1, 1/1/1 -> 2305; round trip over the 16-bit range | identical (tests) |
| COV boundary | B2: report when the change is >= the increment | tests at, below and above the boundary |
| Priority resolution | B4, B2 | tests with slots 5, 8, 16 and relinquish |
| COV report rate | first exit time of a random walk from +-a: mean a^2 / sigma^2 | 5.58 against 5.76 per h at 0.1 (-3.1 %); 13.2 % low at 0.4, where 150 events per run are counted, and 8.3 % low at 0.05, where the 1 s step overshoots the threshold (T9) |
| Polling staleness | longest stale time must equal the interval | 59 s at 60 s, 3600 s at 3600 s (T6) |
| Save study | stale time must not exceed the renewal interval | longest 4.8, 14.7, 29.7, 59.7 min for 5, 15, 30, 60 min (T8) |
| Engine | certain verdicts against injected truth over 1100 + 880 runs | 0 wrong |

What the validation does not cover: the virtual installation is not validated against a
real building, and A3 to A5 could not be checked on a public page.

## 8. FMEA of the design

Failure modes of the diagnosis method.

| # | Failure mode | Effect | Sev. | How it is caught or limited |
|---|---|---|---|---|
| 1 | Gateway restarted, not a settings save | Same signature as COV_SUB_LOST_SAVE (P4) | medium | Report names both; ask whether the device rebooted (its uptime) |
| 2 | Another client writes to the gateway | A held slot not in the BMS command log; PRIORITY_STUCK not certain | medium | Rule requires the BMS log to show the write; otherwise "likely" |
| 3 | The engineer's tool hears only one of two duplicates (unicast I-Am, other subnet) | DUP_DEVICE missed at E3 | high | Who-Is must be broadcast on the BMS's network; noted in the report |
| 4 | KNX monitor started after the event | Healed settings save unprovable at E4 | medium | Engine stays at "likely" from E1 to E3 |
| 5 | A temperature that did not move in the window | Too-coarse increment cannot be shown | low | Nominal step A9 keeps it "likely" instead of "healthy, certain" |
| 6 | Polling interval just above the limit on a slow signal | POLL_TOO_SLOW only likely (6 % of runs at E3) | low | E4 shows the missed changes |
| 7 | Two faults at once | Rules assume P1; ranking may favour the louder fault | medium | Not evaluated |
| 8 | Object list read from the wrong device when two answer | Absence rules misled | medium | The duplicate check runs first and stops the session |

## 9. Verification and validation: plan and results

**Plan.** Unit tests for every requirement that is a protocol or product rule (R-01 to
R-11). Signature tests for every fault. 100 randomised installations per class, each
diagnosed at four levels; 40 more per class at 1 h and at 8 h. COV studies on 20 signal
days per setting, with the random-walk check. A script that fails if a README number is not
in `summary.json`.

| Item | Result | Status |
|---|---|---|
| Tests | 72 passed (`python -m pytest -q`) | pass |
| R-13 no wrong certain verdict | 0 of 1593 (4 h), 0 of 604 (1 h), 0 of 630 (8 h) | pass |
| R-14 healthy | 100 % right and certain at E4; 95 % ranked first at E1 | pass |
| R-16 random-walk rate | 3.1 % at 0.1, 13.2 % worst | pass with a stated tolerance |
| README numbers | `scripts/check_readme_numbers.py` 0 not found | pass |

**What each level can and cannot say** (T1, T2):

| Fault | E1 | Separating evidence | Certain from |
|---|---|---|---|
| Subscriptions lost by a save | identical to lifetime expired (100 %) | client record: subscription still valid; KNX monitor: changes on several points never reported | E4 |
| Lifetime expired | ranked first (tie broken towards it) | client record: lifetime elapsed, no renewal | E3 |
| COV increment too coarse | looks healthy in 51 % | direct read differs by less than 1 while other points update; config increment above the KNX step | E4 |
| Export flag missing / over the limit | looks healthy (22 %, 24 %) or like a moved object | direct read UNKNOWN_OBJECT; object list size equals the limit; config | E4 |
| Type not exportable | ranked first from the point name | config datapoint type | E4 |
| Group address changed | looks healthy in 34 % | object list: new instance with the old name | E3 |
| Priority 8 left | ranked first from the command log | Priority_Array slot 8 still set | E3 |
| Duplicate device | looks healthy in 68 % | two I-Am answers | E3 |
| Polling too slow | ranked first from regular timestamps | client setting | E3 (94 %) |

## 10. Limits and next steps

- Everything is simulated. A3 to A5 come from documentation I could not open myself.
- No network loss, no segmentation, no timeouts; a real slow network would add a cause
  ("notification lost") that confirmed notifications would expose.
- One client, one fault at a time.
- The engine is evaluated on the simulator that produced the faults. Its rules are written
  from protocol premises, not fitted to the data, but the evaluation cannot show how often
  the premises fail in a real building.
- Next with hardware: a controller with a BACnet/IP server on a test board, a BACnet client
  tool and a KNX interface; create each fault, measure the real default increment, the
  export limit and the effect of a settings save on running subscriptions, then run the
  engine on the recordings.
