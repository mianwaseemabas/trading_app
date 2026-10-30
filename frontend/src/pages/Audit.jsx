import React, { useState } from "react";
import { Badge, Card, Modal, JsonView, Table, Tabs, fmt, useApi } from "../components.jsx";

export default function Audit() {
  const [tab, setTab] = useState("audit");
  const [action, setAction] = useState("");
  const { data: audit } = useApi(tab === "audit" ? `/audit?limit=500${action ? `&action=${encodeURIComponent(action)}` : ""}` : null, { deps: [tab, action] });
  const { data: events } = useApi(tab === "events" ? "/events?limit=500" : null, { interval: 15000, deps: [tab] });
  const [detail, setDetail] = useState(null);
  return (
    <div className="stack">
      <Tabs value={tab} onChange={setTab} tabs={[{ k: "audit", label: "Audit trail" }, { k: "events", label: "System events" }]} />
      {tab === "audit" ? (
        <Card title="Audit trail (append-only)" actions={
          <select value={action} onChange={(e) => setAction(e.target.value)}>
            <option value="">All actions</option>
            {["auth", "user", "trading", "risk", "strategy", "order", "position", "live_gate", "backtest", "data"].map((a) => <option key={a} value={a}>{a}.*</option>)}
          </select>
        }>
          <p className="muted small">Records cannot be edited or deleted — the database rejects any change.</p>
          <Table rows={audit} onRow={setDetail} cols={[
            { label: "Time", render: (a) => fmt.dt(a.at) }, { label: "User", k: "username" }, { label: "Action", render: (a) => <code>{a.action}</code> },
            { label: "Entity", render: (a) => (a.entity ? `${a.entity} ${a.entity_id}` : "") }, { label: "IP", k: "ip" },
            { label: "Details", render: (a) => <span className="muted small trunc">{JSON.stringify(a.details)}</span> },
          ]} />
        </Card>
      ) : (
        <Card title="System events">
          <Table rows={events} onRow={setDetail} cols={[
            { label: "Time", render: (e) => fmt.dt(e.at) },
            { label: "Level", render: (e) => <Badge tone={{ ERROR: "bad", WARNING: "warn", INFO: "info" }[e.level]}>{e.level}</Badge> },
            { label: "Source", k: "source" }, { label: "Message", k: "message" },
          ]} />
        </Card>
      )}
      {detail && <Modal title="Details" onClose={() => setDetail(null)}><JsonView data={detail} /></Modal>}
    </div>
  );
}
