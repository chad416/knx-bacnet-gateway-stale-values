# Support case: building management system shows old KNX values

Worked example on a simulated installation (seed index 11 of the COV_SUB_LOST_SAVE class,
`results/support_case.txt` and `summary.json` key `support_case`). Times are seconds from
the start of the 4 h observed window. Written as it would go back to the customer.

---

**Subject:** Old values on the BMS for the KNX floor: root cause and fix

**Your report.** Since this morning, room temperature "temperature 01" on the BMS has shown
20.79 °C, while the room panel shows about 21 °C. Other values on the same page look frozen
too. Nothing was changed on the BMS.

**What we reproduced.** We used your BMS trend export, a BACnet tool on the BMS network, the
client settings from your BMS integrator, the gateway configuration and a KNX group monitor
recording of the same period. The installation has 16 BMS points on one KNX controller with
a BACnet/IP server. The BMS uses COV subscriptions with lifetime 0 (never expires) and does
not renew them, with unconfirmed notifications.

**What each step could and could not tell.**

1. *BMS history only (E1).* All 16 points stopped updating at about t = 6960 s. Before that,
   the points updated often enough that a common silence of more than 2 hours by chance is
   impossible (estimated chance 6e-86). This shows that the gateway or the subscriptions
   stopped, not the sensors. It cannot tell which: an expired subscription and subscriptions
   cancelled at the gateway look exactly the same here. In our simulated tests this pair was
   identical in 100 % of the cases.
2. *One direct read (E2).* Reading Present_Value of "temperature 01" directly returns
   21.14 °C, while the BMS still shows 20.79 °C. The gateway has the right value; it is the
   reporting to the BMS that stopped. The difference is 0.35 K, below 1, so a too-coarse COV
   increment was still possible at this step.
3. *Object list and client settings (E3).* One device answered Who-Is (no duplicate). The
   object exists under its instance. The BMS subscribed with lifetime 0, so the
   subscription cannot have expired. The cause must be at the gateway side: the
   subscriptions were dropped there, or the increment is too coarse.
4. *Gateway configuration and KNX monitor (E4).* The COV increment of "temperature 01" is
   0.1 K, which matches the sensor. The KNX monitor shows that 15 of the 16 points changed by
   more than their increment allows after t = 7377 s, and no notification reached the BMS.
   The objects exist and the BMS's subscriptions are valid on its side. So the gateway no
   longer had the subscriptions.

**Root cause.** The gateway's COV settings were saved at about t = 7018 s. The controller's
user guide states that saving changed COV increments cancels all existing COV subscriptions,
which "must be restarted from the client side", and also resets the priority arrays of all
objects. Your BMS subscribes once with an unlimited lifetime and never renews, so it was
never told and has shown frozen values since. A restart of the gateway would probably have
had the same effect; the gateway's uptime would tell the two apart.

**A second effect you may not have noticed.** At t = 6574 s the BMS wrote a setpoint of
24.5 °C at priority 8 to "setpoint 16". The gateway writes KNX values into slot 9, so the
command was in force and was sent to the room. The settings save cleared the command. From
then on the gateway showed 22.0 °C for this point: the value it had when the BMS first read
it at commissioning, which the gateway keeps as Relinquish_Default and never updates. The
room itself still ran 24.5 °C, and the BMS still showed 24.5 °C. For about 31 minutes the
three disagreed. The BMS's relinquish at t = 8103 s was acknowledged but had nothing to
release. At t = 8852 s someone set 20.0 °C on the room panel, and from then on the gateway
followed the panel again (20.0 °C at the end of the window). The BMS still shows 24.5 °C and
believes its command holds.

**Fix.**

1. Now: make the BMS subscribe again (restart its BACnet driver or re-add the device). It
   gets the current value of every point at once.
2. Re-send any BMS commands that should be active. Until then, a point whose room panel has
   not been touched since the save shows its commissioning value on the gateway.
3. Permanently: set the BMS to renew COV subscriptions with a finite lifetime, renewed every
   300 to 900 s. In our study this limits the stale time after such an event to 2.0 min on
   average at 300 s (6.4 min at 900 s), for 1.0 or 0.33 messages per second with 150
   objects.
4. When COV settings must be changed on the gateway, plan it with the BMS integrator and
   re-subscribe right after.

**How it was confirmed.** In the simulated case, the engine's verdict became "certain" only
with the KNX monitor (E4). The re-subscription in step 1 sends an immediate notification
for every point; the BMS values then match a direct read of the gateway. On a real site we
would check that the next KNX change of "temperature 01" reaches the BMS within seconds.

---

Note: simulated case. The cancelled subscriptions, the reset of the priority arrays, the
slot for KNX values and the frozen Relinquish_Default are stated in the controller's user
guide (docs/SOURCES.md, G2 and G3). That the reset also empties the gateway's own slot, so
that the commissioning value shows, is my reading of the guide (DOSSIER A5). Times are from
`summary.json` key `support_case`.
