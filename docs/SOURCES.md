# Sources

Every rule of the server and client model comes from one of these documents or is listed as
an assumption at the end. The column "taken" says exactly what was used. All pages were
opened and read on 19 November 2026. The BACnet standard itself (ANSI/ASHRAE 135) is sold,
not free; I did not read it and quote nothing from it. Each standard rule below is taken
from an openly readable reference instead, and the clause number is given only where that
reference names it.

| Id | Document | Where it was read | Taken |
|---|---|---|---|
| B1 | Public knowledge base of the controller platform, page "BACnet server" | https://kb.logicmachine.net/integration/bacnet-server/ | Binary and numeric objects with the export property enabled can be accessed via BACnet; the export property is set in the objects list. Binary objects map to Binary Value, numeric objects to Analog Value. Instance number = group address in numeric form, X/Y/Z => X * 2048 + Y * 256 + Z, examples 0/0/1 => 1 and 1/1/1 => 2305. The COV delta is set per numeric object in the BACnet COV settings. Maximum number of COV subscriptions: default 256, can be raised to 4096. Recommended number of BACnet objects: 2000 or fewer. The page does **not** state the default COV increment, the priority-array behaviour, or what happens to data types other than binary and numeric. |
| B2 | Open-source BACnet protocol stack (MIT licence), `src/bacnet/basic/object/av.c` | https://raw.githubusercontent.com/bacnet-stack/bacnet-stack/master/src/bacnet/basic/object/av.c | COV detection for an Analog Value: change when `cov_delta >= cov_increment`, where the delta is taken against the value at the last report (`Prior_Value`), which is then updated. Present_Value resolution: the first active slot from priority 1 downward wins, else Relinquish_Default. WriteProperty errors: unknown object = error class OBJECT, code UNKNOWN_OBJECT; unknown property = PROPERTY / UNKNOWN_PROPERTY; a known but not writable property = WRITE_ACCESS_DENIED. Used as an executable reading of the standard, not as the standard. |
| B3 | The same stack, `src/bacnet/basic/service/h_cov.c` (SubscribeCOV handler) | https://raw.githubusercontent.com/bacnet-stack/bacnet-stack/master/src/bacnet/basic/service/h_cov.c | A subscription's lifetime is counted down and the subscription is deleted at 0; lifetime 0 means it never expires. A subscription with the same object, process id and address is refreshed (new lifetime), not duplicated. A new or refreshed subscription is flagged to send a notification at once. Confirmed or unconfirmed notifications as requested in the subscription. Full table answers with error class RESOURCES; unknown object with UNKNOWN_OBJECT. |
| B4 | OPC Foundation, OPC 30030 *BACnet* companion specification, section 3.2.1 *Command Prioritization* | https://reference.opcfoundation.org/specs/OPC-30030/3.2.1 | 16 priority levels, 1 the highest; the value in the highest-priority slot takes precedence; Relinquish_Default is used when all slots are NULL. |
| B5 | BACnet protocol reference pages of a gateway maker: *SubscribeCOV service* and *COV* | https://docs.chipkin.com/protocols/bacnet/services/subscribe-cov/ and https://docs.chipkin.com/protocols/bacnet/cov/ | SubscribeCOV is defined in clause 13.14 of ANSI/ASHRAE 135-2024. Lifetime in seconds, 0 requests an indefinite subscription; devices must accept lifetimes of at least 28800 s. The device sends an initial notification so the client starts with the current value; a matching resubscription is a refresh; a client re-sends SubscribeCOV more often than the lifetime. Errors: OBJECT/UNKNOWN_OBJECT, OBJECT/OPTIONAL_FUNCTIONALITY_NOT_SUPPORTED, RESOURCES/NO_SPACE_TO_ADD_LIST_ELEMENT, SERVICES/VALUE_OUT_OF_RANGE. COV produces traffic only when subscribed values change, polling on every interval. Secondary source. |
| B6 | Same reference: *BACnet Device Instance* | https://docs.chipkin.com/protocols/bacnet/device-instance/ | Assignable device instances 0 to 4 194 302, 4 194 303 is the wildcard. Device instances must be unique across the internetwork; duplicates cause discovery confusion, wrong-device behaviour and intermittent failures. Clients tell devices apart by the instance in Who-Is / I-Am. |
| B7 | Article *2 BACnet MS/TP devices with the same device ID* by the same gateway maker | https://store.chipkin.com/articles/2-bacnet-mstp-devices-with-the-same-device-id | The most recent I-Am or response replaces earlier bindings for the same device instance ("last one wins"); the client sees inconsistent values and unexpected bindings. The article is about MS/TP; the model applies the same binding rule on BACnet/IP (assumption A11). |
| B8 | ANSI/ASHRAE Standard 135 *BACnet* | not read (paid standard) | Nothing taken directly. Cited only so the reader knows which document is authoritative for COV (clause 13), SubscribeCOV (13.14, per B5) and the priority mechanism. |
| G | User guide of the modelled KNX logic controller, firmware 3.0: pages on BACnet settings, BACnet objects and BACnet COV settings | public online user guide of the controller's maker, read page by page in a browser | The sentences quoted under G1 to G3 below. The maker and the product are not named on purpose, so that this repository stays generic. The guide is public. |

### What the user guide says (G1 to G3)

The sentences below are copied word for word from the guide. Ids G1 to G3 are used in the
code, the tests and the dossier.

**G1, exported objects and limits.**

- "Selecting the Export checkbox makes the specific KNX object visible in BACnet as a BACnet
  object. Binary objects appear as binary values, while numeric objects appear as analog
  values. Other data types are not supported."
- "BACnet Server | Up to 150 exported objects | *No limit (≤ 2000 recommended)", for the
  small and the large controller.
- "Performance testing was measured on HW3.0 when the object value change interval was set to
  60 s. The recommended limit for HW1.x remains 500 objects."
- The page on BACnet objects lists the objects with their parameters and offers them as a
  CSV download. It says nothing about how an instance number is formed.

**G2, BACnet settings.**

- "Device ID: Ensure that the BACnet device ID is unique on the BACnet network."
- "Object priority: Define the priority array to which the controller will write. The
  controller writes to the Relinquish Default (RD) property by first reading (upload from
  BMS) only. It takes the current value of the object. The value of the RD property cannot
  be changed afterward. If an object read from the controller has a higher value than the RD
  property, it raises the Overwritten flag."
- "Port: Specify the BACnet port (default is 47808)."
- "BBMD Configuration: Set the BACnet router IP (BBMD IP) and port (BBMD port). Configure the
  BBMD lease time (seconds) for registration resend intervals."

**G3, BACnet COV settings.**

- "Each analog value that is active for COV subscription has a parameter called COV
  increment. This parameter defines the minimal change of value (delta) that triggers a
  value update on the client side. By default, the COV increment is set to 1. This means
  that unless the value in the controller changes by more than 1, the client-side value
  remains unchanged."
- "If you modify the COV increment parameters ... and save the changes, all existing COV
  subscriptions will be canceled. To resume COV subscriptions, they must be restarted from
  the client side."
- "IMPORTANT: Changing COV increment values also resets the priority array values of all
  objects."

**How I read G2 on the priority array.** "Define the priority array to which the controller
will write" names one configurable slot of the 16-slot priority array; the controller writes
each KNX value there. Relinquish_Default is written once, at the object's first read by a
client, with the object's value at that moment, and never again. The last sentence, on the
"Overwritten flag", is unclear as written: a "higher value" may mean a higher priority, so
that the flag marks an object whose present value comes from a slot rather than from
Relinquish_Default. I do not model the flag and no finding rests on it (open point O1 in
DOSSIER section 7.1).

**One wording difference.** G3 says the client value stays unchanged "unless the value in the
controller changes by more than 1". B2 reports at a change of at least the increment (`>=`).
The model keeps `>=`. The two differ for a change of exactly the increment. That case is
common when the increment equals the sensor's send step, because a send-on-delta sensor
mostly moves by exactly its step (table T12). The guide's sentence is an explanation, not a
rule statement, so I keep it as open point O2 and do not change the model.

## Rules that rest only on my own assumption

The following are not stated in any source I read. All are listed in DOSSIER section 7.1,
and each one that carries a finding is named there. The bench tests that would replace them
are in [BENCH_TEST_PLAN.md](BENCH_TEST_PLAN.md).

- **Which objects are dropped above the limit** (A2). The model keeps the lowest instance
  numbers. The diagnosis rules do not depend on this choice.
- **Which slot the controller writes KNX values to by default** (A4). G2 makes it a setting
  and gives no default. The model draws it per installation.
- **Whether the reset of G3 also empties the controller's own slot** (A5). G3 says "resets
  the priority array values of all objects"; I read that as all 16 slots. If the controller
  refilled its slot at once, the fallback to Relinquish_Default after a save would last no
  time; the loss of BMS commands and subscriptions would be the same.
- **How the instance number is formed** (A15). B1, the platform's public page, gives
  X * 2048 + Y * 256 + Z; the guide pages I read do not. The model uses B1.
- **What happens to a BACnet write on the KNX side** (A16). Not stated in G2. The model sends a
  change of Present_Value caused by a BACnet write to the group address, and the sent value
  becomes the controller's own KNX value, so it lands in the controller's slot.
- Whether a change of exactly the increment reports (see the wording difference above).
- Signal statistics, object mix, window length and client behaviour (A6 to A14): my own
  assumptions, with sensitivity runs where they decide a finding.
