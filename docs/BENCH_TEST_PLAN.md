# Bench test plan

Tests on a real controller that would replace each assumption that still carries a finding,
and each open point of the user guide that matters here. Ids refer to DOSSIER section 7.1
(A = assumption, O = open point) and to docs/SOURCES.md (G1 to G3).

**Set-up.** One KNX logic controller with its BACnet/IP server switched on, firmware 3.0, on a
test board with a KNX power supply. A PC on the same IP subnet with a BACnet client tool
(Who-Is, ReadProperty, WriteProperty with priority, SubscribeCOV with lifetime) and a KNX bus
tool with a group monitor. Both tools log with timestamps. Test objects: `S` a setpoint,
datapoint type 9.001, group address 3/0/1, export on; `T` a temperature, 9.001, 3/0/2,
export on; `B` a switch, 1.001, 1/0/1, export on.

Run P1 first. It settles the priority behaviour end to end, on which three fault classes and
the save study rest.

| Id | Replaces | Configure | Write or subscribe | Read back | Pass if | Time |
|---|---|---|---|---|---|---|
| P1 | G2 reading, A4, A16, O1 | Object priority = 10. Do not read `S` from any client yet. | KNX: 21.0, then 21.5 on 3/0/1. Then the first ReadProperty of `S`. KNX: 22.0. | Present_Value, Priority_Array, Relinquish_Default of `S` after each step; the Overwritten flag in the configurator | Slot 10 holds each KNX value; Relinquish_Default = 21.5 and stays 21.5 | 5 min |
| P1a | same | as P1 | WriteProperty `S` = 24.0 at priority 8. KNX: 22.5. Then relinquish (NULL) at 8. | Present_Value, Priority_Array; group monitor | 24.0 wins and masks 22.5; after the relinquish 22.5 wins. Note whether 24.0 is sent to 3/0/1 and whether slot 10 then holds 24.0 (A16) | 5 min |
| P1b | same | as P1 | WriteProperty `S` = 18.0 at priority 16. | Present_Value, Priority_Array; group monitor | Present_Value stays at the KNX value; slot 16 holds 18.0; nothing is sent to KNX ("does nothing") | 3 min |
| P1c | same | as P1 | WriteProperty `S` = 24.0 at priority 10. KNX: 23.0. Relinquish at 10. | Present_Value, Priority_Array; group monitor | 23.0 overwrites 24.0 in slot 10; after the relinquish Present_Value = 21.5 (frozen Relinquish_Default). Note any telegram sent to KNX | 5 min |
| P2 | A4 | Reset a spare controller to factory settings, or open a new project | none | Object priority in the BACnet settings | Gives the default slot. If it is 16, the "no effect" class needs a deliberate change; if it is 8, BMS commands at 8 share the slot | 2 min |
| P3 | A5, G3 | Object priority 16. Client subscribes to `T` and `B` with lifetime 0 and to `S` with lifetime 300 s; `S` commanded 24.0 at priority 8. | Change the COV increment of `T` from 1 to 0.1 and save. Then KNX: a new value on 3/0/2 and on 1/0/1. | Notifications in the client log; Priority_Array and Present_Value of `S`, `T`, `B` right after the save and after the new telegrams; group monitor during the save | No notification after the save (G3). Priority_Array all empty, or slot 16 refilled at once (decides A5). Present_Value of `S` = its Relinquish_Default until a KNX telegram arrives. Nothing sent to KNX at the save | 15 min |
| P4 | O2, G3 wording | COV increment of `T` = 0.1 | Client subscribes to `T`. KNX on 3/0/2: 21.00, 21.10, 21.20, 21.25, 21.30 | Notifications | A notification at 21.10 means "at least the increment" (as modelled); none until 21.20 or 21.25 means "more than". If "more than", set increments just below the sensor step | 5 min |
| P5 | A6 | as P4 | Subscribe to `T` with lifetime 120 s; re-subscribe after 60 s with no value change | Notifications | An immediate notification on each subscribe and re-subscribe | 3 min |
| P6 | A15, O3 | Export group objects 0/0/1, 1/1/1, 31/7/255 (any binary type) | none | Object_List; download the CSV object list from the configurator | Instances 1, 2305 and 65535 confirm the formula of B1 | 5 min |
| P7 | A2 | Small controller. Flag 160 binary objects for export, group addresses spread over several main groups | none | Object_List; CSV object list | Exactly 150 present (G1). Which 10 are absent: lowest instances kept (as modelled), first created kept, or something else | 20 min |
| P8 | A11 | A second BACnet device (a spare controller or a device simulator on the PC) with the same device ID on the same subnet | Broadcast Who-Is from the client; let both answer; restart one of them | The client's binding (address per device ID) after each I-Am | The binding follows the last I-Am (as modelled for BACnet/IP) or stays with the first | 15 min |
| P9 | P4 premise | Subscriptions as in P3, a command at priority 8 on `S` | Restart the controller (power cycle) | Notifications; Priority_Array of `S`; Relinquish_Default of `S` | Tells a restart from a settings save: if a restart also cancels subscriptions and empties the arrays, the two remain indistinguishable without the uptime | 10 min |

Total about 1 h 30 min, plus set-up.

**Not planned.** Foreign-device registration (G2, BBMD settings) is not modelled; test it only
on a site that uses it: register the controller with a BBMD at a short lease, stop the BBMD,
and time how long the controller's broadcasts are missing on the BMS network.
