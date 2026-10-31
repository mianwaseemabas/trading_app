import React, { useEffect, useLayoutEffect, useState } from "react";
import { TOURS } from "../tours.js";

function find(sel) {
  if (sel.startsWith("text:")) {
    const t = sel.slice(5);
    return [...document.querySelectorAll(".card-h h3, h2, h3, b")].find((e) => e.textContent.includes(t))?.closest(".card") || null;
  }
  return document.querySelector(sel);
}

export default function Tour() {
  const [tour, setTour] = useState(null);
  const [i, setI] = useState(0);
  const [rect, setRect] = useState(null);
  useEffect(() => {
    const h = (e) => { if (TOURS[e.detail]) { setTour(e.detail); setI(0); } };
    window.addEventListener("q-tour", h);
    return () => window.removeEventListener("q-tour", h);
  }, []);
  const step = tour ? TOURS[tour].steps[i] : null;
  useLayoutEffect(() => {
    if (!step) return undefined;
    const cur = (window.location.hash.replace(/^#\/?/, "") || "terminal").split("?")[0];
    if (cur !== step.route) window.location.hash = `#/${step.route}`;
    setRect(null);
    let tries = 0, el = null;
    const t = setInterval(() => {
      el = find(step.sel);
      tries += 1;
      if (el || tries > 30) {
        clearInterval(t);
        if (el) {
          el.scrollIntoView({ block: "center", behavior: "smooth" });
          setTimeout(() => { const r = el.getBoundingClientRect(); setRect({ x: r.left, y: r.top, w: r.width, h: r.height }); }, 450);
        } else setRect({ x: window.innerWidth / 2 - 100, y: 120, w: 200, h: 0 });
      }
    }, 120);
    const onResize = () => { if (el) { const r = el.getBoundingClientRect(); setRect({ x: r.left, y: r.top, w: r.width, h: r.height }); } };
    window.addEventListener("resize", onResize);
    window.addEventListener("scroll", onResize, true);
    return () => { clearInterval(t); window.removeEventListener("resize", onResize); window.removeEventListener("scroll", onResize, true); };
  }, [tour, i]); // eslint-disable-line
  useEffect(() => {
    if (!tour) return undefined;
    const k = (e) => { if (e.key === "Escape") setTour(null); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [tour]);
  if (!step) return null;
  const n = TOURS[tour].steps.length;
  const vw = window.innerWidth, vh = window.innerHeight;
  const pad = 6;
  const box = rect && { left: rect.x - pad, top: rect.y - pad, width: rect.w + 2 * pad, height: rect.h + 2 * pad };
  const tipW = Math.min(340, vw - 24);
  let tip = { left: 12, top: 80 };
  if (box) {
    const below = box.top + box.height + 12;
    const top = below + 190 < vh ? below : Math.max(12, box.top - 200);
    tip = { left: Math.max(12, Math.min(vw - tipW - 12, box.left)), top: Math.min(top, vh - 210) };
  }
  return (
    <div className="tour">
      {box ? <div className="tour-hole" style={box} /> : <div className="tour-dim" />}
      <div className="tour-tip" style={{ ...tip, width: tipW }}>
        <div className="tiny muted">{TOURS[tour].title} · {i + 1}/{n}</div>
        <b>{step.title}</b>
        <p className="small">{step.text}</p>
        <div className="row between">
          <button className="btn ghost sm" onClick={() => setTour(null)}>Close</button>
          <div className="row gap">
            <button className="btn sm" disabled={i === 0} onClick={() => setI(i - 1)}>← Back</button>
            {i + 1 < n ? <button className="btn primary sm" onClick={() => setI(i + 1)}>Next →</button>
              : <button className="btn primary sm" onClick={() => setTour(null)}>Done ✓</button>}
          </div>
        </div>
      </div>
    </div>
  );
}
