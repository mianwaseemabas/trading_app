import React from "react";
import { AutomationCard } from "./StrategyLab.jsx";

export default function AutoTrade() {
  return (
    <div className="stack">
      <p className="muted small">
        Turn automatic trading on or off for each account. Practice and demo switch on with one click; real money needs an approved
        live gate (Risk &amp; Strategy) and your password. The risk limits of each account still apply to every automatic order.
      </p>
      <AutomationCard />
    </div>
  );
}
