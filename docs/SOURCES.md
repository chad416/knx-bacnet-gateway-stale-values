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

## Rules that rest only on the research notes or on my own assumption

The following could not be confirmed from an openly readable source. They come from a user
guide of a controller built on the same platform, which this repository does not name, as it
was summarised in my research notes, or they are my own choice. All are listed as
assumptions in DOSSIER section 7.1, and each one that carries a finding is named there.

- **Export limit of 150 objects** for the small controller variant, and "no hard limit, 2000
  or fewer recommended" for the larger one (A1). B1 confirms only the 2000 recommendation.
- **Which objects are dropped above the limit** (A2). Not documented anywhere I found. The
  model keeps the lowest instance numbers. The diagnosis rules do not depend on this choice.
- **Default COV increment of 1** (A3). B1 says the increment is set per object but gives no
  default.
- **Saving the COV settings cancels all subscriptions and resets all priority arrays** (A5).
  Not in B1. The mechanism (a service restart) is not documented either.
- **"Other data types are not supported"** for export (date, time, text). B1 only says that
  binary and numeric objects can be accessed, which implies the rest cannot.
- **What a KNX telegram does while a BACnet command is held** (A4). Not stated in any
  source. The model treats KNX values as Relinquish_Default.
- Whether a change of exactly the increment reports. B2 uses `>=`; the standard text was
  not read.
- Signal statistics, object mix, window length and client behaviour (A6 to A12): my own
  assumptions, with sensitivity runs where they decide a finding.
