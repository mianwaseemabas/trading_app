import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../App.jsx";
import { Badge, Card, fmt, useAction, useApi, useToast } from "../components.jsx";
import { LESSON_TOUR, startTour } from "../tours.js";

const lessonFromHash = () => {
  const m = window.location.hash.match(/[?&]l=([a-z0-9-]+)/);
  return m ? m[1] : null;
};
const viewFromHash = () => (window.location.hash.match(/[?&]v=(exam|cert)/) || [])[1] || null;
const go = (lid) => { window.location.hash = lid ? `#/academy?l=${lid}` : "#/academy"; };

function Progress({ done, total }) {
  const pct = total ? Math.round((done / total) * 100) : 0;
  return <div className="meter-t" title={`${pct}%`}><div className="meter-f ok" style={{ width: `${pct}%` }} /></div>;
}

function Tutor({ lessonId, lessonTitle }) {
  const { can } = useAuth();
  const toast = useToast();
  const [msgs, setMsgs] = useState([]);
  const [text, setText] = useState("");
  const [simple, setSimple] = useState(true);
  const [busy, setBusy] = useState(false);
  const end = useRef(null);
  useEffect(() => { setMsgs([]); }, [lessonId]);
  useEffect(() => { end.current?.scrollIntoView({ block: "nearest" }); }, [msgs]);
  if (!can("ai.use")) return null;
  const ask = async (q) => {
    const content = (q || text).trim();
    if (!content || busy) return;
    const next = [...msgs, { role: "user", content }];
    setMsgs(next); setText(""); setBusy(true);
    try {
      const r = await api.post("/academy/tutor", { lesson_id: lessonId, messages: next, simple });
      setMsgs([...next, { role: "assistant", content: r.reply }]);
    } catch (e) {
      toast(e.message, "err");
      setMsgs(msgs);
    } finally { setBusy(false); }
  };
  const quick = lessonId
    ? ["Explain this lesson more simply", "Give me a real example with numbers", "What exactly do I click next?", "Explain in Urdu"]
    : ["Where should I start?", "Which platform should I start trading on?", "What does my setup still need?", "Explain in Urdu"];
  return (
    <Card title={`🤖 Ask the tutor${lessonTitle ? ` · ${lessonTitle}` : ""}`} className="tutor" actions={
      <label className="check small"><input type="checkbox" checked={simple} onChange={(e) => setSimple(e.target.checked)} /> explain like I'm new</label>
    }>
      <div className="tutor-log">
        {msgs.length === 0 && <p className="small muted">Ask anything — the tutor knows this lesson, your app settings and what you've already set up. It explains; it never places trades.</p>}
        {msgs.map((m, i) => <div key={i} className={`bubble ${m.role}`}>{m.content}</div>)}
        {busy && <div className="bubble assistant muted">Thinking…</div>}
        <div ref={end} />
      </div>
      <div className="chips">{quick.map((q) => <button key={q} className="chip ask" disabled={busy} onClick={() => ask(q)}>{q}</button>)}</div>
      <form className="row gap" onSubmit={(e) => { e.preventDefault(); ask(); }}>
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Type your question…" style={{ flex: 1 }} />
        <button className="btn primary" disabled={busy || !text.trim()}>Ask</button>
      </form>
    </Card>
  );
}

function Quiz({ lesson, onScored }) {
  const [ans, setAns] = useState({});
  const [res, setRes] = useState(null);
  const [run, busy] = useAction();
  useEffect(() => { setAns({}); setRes(null); }, [lesson.id]);
  if (!lesson.quiz.length) return null;
  const all = lesson.quiz.every((_, i) => ans[i] !== undefined);
  return (
    <Card title={`✏️ Quick check${lesson.quiz_score === 100 ? " — passed ✓" : ""}`}>
      {lesson.quiz.map((q, i) => (
        <div key={i} className="quiz-q">
          <b>{i + 1}. {q.q}</b>
          <div className="quiz-opts">
            {q.options.map((o, j) => {
              const r = res?.results[i];
              const cls = r ? (j === r.answer ? "right" : ans[i] === j ? "wrong" : "") : ans[i] === j ? "picked" : "";
              return <button key={j} className={`quiz-opt ${cls}`} onClick={() => { if (!res) setAns({ ...ans, [i]: j }); }}>{o}</button>;
            })}
          </div>
          {res && <div className={`small ${res.results[i].correct ? "pos" : "neg"}`}>{res.results[i].correct ? "✓ Correct. " : "✕ Not quite. "}<span className="muted">{res.results[i].why}</span></div>}
        </div>
      ))}
      {!res
        ? <button className="btn primary" disabled={!all || busy} onClick={() => run(() => api.post(`/academy/lessons/${lesson.id}/quiz`, { answers: lesson.quiz.map((_, i) => ans[i]) })).then((r) => { setRes(r); onScored(); }).catch(() => {})}>Check my answers</button>
        : res.passed ? <p className="pos">🎉 All correct!</p>
          : <button className="btn" onClick={() => { setRes(null); setAns({}); }}>Try again</button>}
    </Card>
  );
}

function Lesson({ lid, onBack }) {
  const { data: l, reload } = useApi(`/academy/lessons/${lid}`, { deps: [lid] });
  const [run, busy] = useAction();
  useEffect(() => { window.scrollTo(0, 0); }, [lid]);
  if (!l) return <p className="muted">Loading…</p>;
  const toggle = (i) => run(() => api.post(`/academy/lessons/${lid}/steps`, { step: i, done: !l.steps_done.includes(i) })).then(reload).catch(() => {});
  const canFinish = !l.quiz.length || l.quiz_score === 100;
  return (
    <div className="academy-lesson">
      <div className="stack">
        <div className="row gap wrap small">
          <button className="btn ghost sm" onClick={onBack}>← All lessons</button>
          <span className="muted">{l.course.title}</span>
        </div>
        <Card title={<>{l.title} {l.done && <Badge tone="good">done</Badge>}</>} actions={<span className="small muted">⏱ {l.minutes} min</span>}>
          <div className="lesson-goal">🎯 <b>Goal:</b> {l.goal}</div>
          {LESSON_TOUR[lid] && <button className="btn sm" style={{ marginBottom: 10 }} onClick={() => startTour(LESSON_TOUR[lid])}>▶ Show me in the app</button>}
          <div className="lesson-idea"><div className="tiny muted">THE IDEA</div>{l.explain}</div>
          {l.check_label && (
            <div className={`alert ${l.check_ok ? "ok" : "warn"} small`}>
              {l.check_ok ? "✓ The app sees: " : "○ Not yet: "}{l.check_label.toLowerCase()}.
            </div>
          )}
        </Card>
        <Card title="👣 Do it step by step">
          <ol className="lesson-steps">
            {l.steps.map((s, i) => {
              const done = l.steps_done.includes(i);
              return (
                <li key={i} className={done ? "done" : ""}>
                  <button className="step-check" disabled={busy} onClick={() => toggle(i)} title="Mark done">{done ? "✓" : i + 1}</button>
                  <div>
                    <div>{s.text}</div>
                    {s.link && <a className="btn ghost sm" href={s.link}>Open this page →</a>}
                  </div>
                </li>
              );
            })}
          </ol>
          <p className="tiny muted">Tip: open the page, do the step, then come back here (Academy keeps your place) and tick it.</p>
        </Card>
        <Quiz lesson={l} onScored={reload} />
        <div className="row between wrap gap">
          <button className="btn ghost" disabled={!l.prev} onClick={() => go(l.prev)}>← Previous</button>
          {!l.done
            ? <button className="btn primary" disabled={!canFinish || busy} title={canFinish ? "" : "Pass the quick check first"}
                onClick={() => run(() => api.post(`/academy/lessons/${lid}/complete`), "Lesson complete 🎉").then(() => (l.next ? go(l.next) : onBack())).catch(() => {})}>
                ✓ Finish lesson{l.next ? " & continue" : ""}
              </button>
            : <button className="btn primary" disabled={!l.next} onClick={() => go(l.next)}>Next lesson →</button>}
        </div>
      </div>
      <Tutor lessonId={lid} lessonTitle={l.title} />
    </div>
  );
}

function Exam({ onBack }) {
  const [ex, setEx] = useState(null);
  const [ans, setAns] = useState({});
  const [res, setRes] = useState(null);
  const [run, busy] = useAction();
  const start = () => run(() => api.post("/academy/exam/start")).then((e) => { setEx(e); setAns({}); setRes(null); window.scrollTo(0, 0); }).catch(() => {});
  useEffect(() => { start(); }, []); // eslint-disable-line
  if (!ex) return <p className="muted">Preparing your exam…</p>;
  const all = ex.questions.every((q) => ans[q.id] !== undefined);
  const byId = Object.fromEntries((res?.results || []).map((r) => [r.id, r]));
  return (
    <div className="stack">
      <div className="row gap wrap"><button className="btn ghost sm" onClick={onBack}>← Academy</button></div>
      <Card title={`🎓 Readiness exam — ${ex.questions.length} questions, pass with ${ex.pass_pct}%`}>
        <p className="small muted">Questions are picked at random from all lessons. Take your time; you can retake it as often as you like (new questions each time).
          Passing it is one of the live-gate requirements before real money.</p>
        {res && <div className={`alert ${res.passed ? "ok" : "warn"}`}><b>{res.passed ? `🎉 Passed with ${res.score}%!` : `${res.score}% — not yet (${res.pass_pct}% needed).`}</b>
          {res.passed ? " Finish all lessons to unlock your certificate." : " Read the explanations below, review those lessons, then try again."}</div>}
        {ex.questions.map((q, i) => {
          const r = byId[q.id];
          return (
            <div key={q.id} className="exam-q">
              <b>{i + 1}. {q.q}</b> <span className="tiny muted">· {q.lesson}</span>
              <div className="quiz-opts" style={{ marginTop: 6 }}>
                {q.options.map((o, j) => {
                  const cls = r ? (j === r.answer ? "right" : ans[q.id] === j ? "wrong" : "") : ans[q.id] === j ? "picked" : "";
                  return <button key={j} className={`quiz-opt ${cls}`} onClick={() => !res && setAns({ ...ans, [q.id]: j })}>{o}</button>;
                })}
              </div>
              {r && !r.correct && <div className="small muted" style={{ marginTop: 4 }}>{r.why}</div>}
            </div>
          );
        })}
        {!res ? <button className="btn primary" disabled={!all || busy} onClick={() => run(() => api.post(`/academy/exam/${ex.exam_id}/submit`, { answers: ans })).then((r) => { setRes(r); window.scrollTo(0, 0); }).catch(() => {})}>
          Submit exam ({Object.keys(ans).length}/{ex.questions.length})</button>
          : <button className="btn" onClick={start}>Take a new exam</button>}
      </Card>
    </div>
  );
}

function Certificate({ onBack }) {
  const { data, error } = useApi("/academy/certificate");
  if (error) return <div className="stack"><button className="btn ghost sm" onClick={onBack}>← Academy</button><div className="alert warn">{error}</div></div>;
  if (!data) return <p className="muted">Loading…</p>;
  return (
    <div className="stack">
      <div className="row gap wrap"><button className="btn ghost sm" onClick={onBack}>← Academy</button><button className="btn primary sm" onClick={() => window.print()}>🖨 Print / save as PDF</button></div>
      <div className="cert-wrap">
        <div className="cert">
          <small>QIVYNEX ACADEMY</small>
          <h1>Certificate of Completion</h1>
          <small>This certifies that</small>
          <div className="who">{data.name}</div>
          <p style={{ maxWidth: 620, margin: 0 }}>completed all {data.lessons} lessons of the Qivynex Trade Academy — safe trading, risk management, strategy testing and
            automation — and passed the readiness exam with <b>{data.score}%</b>.</p>
          <div className="seal">Q</div>
          <small>Completed {fmt.dt(data.completed)} · Certificate {data.cert_id} · Longest learning streak {data.streak?.best || 1} day(s)</small>
          <small>Education only — it shows you learned how to use the app safely. It is not a financial licence or advice.</small>
        </div>
      </div>
    </div>
  );
}

export default function Academy() {
  const [view, setView] = useState(viewFromHash());
  const [lid, setLid] = useState(lessonFromHash());
  const { data, reload } = useApi("/academy");
  const [open, setOpen] = useState(null);
  useEffect(() => {
    const h = () => { setLid(lessonFromHash()); setView(viewFromHash()); reload(); };
    window.addEventListener("hashchange", h);
    return () => window.removeEventListener("hashchange", h);
  }, [reload]);
  if (view === "exam") return <Exam onBack={() => go(null)} />;
  if (view === "cert") return <Certificate onBack={() => go(null)} />;
  if (lid) return <Lesson lid={lid} onBack={() => go(null)} />;
  if (!data) return <p className="muted">Loading…</p>;
  const pct = data.total ? Math.round((data.done / data.total) * 100) : 0;
  return (
    <div className="stack">
      <div className="academy-hero">
        <div>
          <div className="row between wrap gap"><span className="tiny muted">QIVYNEX ACADEMY</span>
            <span className="streak" title={`Best streak ${data.streak.best} days · learned on ${data.streak.days} days`}>🔥 {data.streak.current} day{data.streak.current === 1 ? "" : "s"}{data.streak.today ? "" : " — learn today to keep it"}</span></div>
          <h2>Learn the app step by step — from zero to safe trading</h2>
          <p className="small muted">{data.total} short lessons. Each one: the idea in plain words, steps to do in the app, a quick check, and a tutor (Claude) you can ask anything.</p>
          <div className="row gap wrap" style={{ alignItems: "center" }}>
            <div style={{ flex: 1, minWidth: 160 }}><Progress done={data.done} total={data.total} /></div>
            <b>{pct}%</b><span className="small muted">{data.done}/{data.total} lessons</span>
          </div>
          <div className="row gap wrap" style={{ marginTop: 12 }}>
            {data.next && <button className="btn primary" onClick={() => go(data.next.id)}>{data.done ? "Continue" : "Start"}: {data.next.title} →</button>}
            <button className="btn" onClick={() => startTour("start")}>▶ Take the app tour</button>
            <button className="btn" onClick={() => { window.location.hash = "#/academy?v=exam"; }}>🎓 {data.exam.passed ? `Exam passed (${data.exam.score}%) — retake` : "Readiness exam"}</button>
            {data.certificate && <button className="btn" onClick={() => { window.location.hash = "#/academy?v=cert"; }}>📜 My certificate</button>}
          </div>
          {!data.next && <p className="pos">🎓 You finished every lesson.{data.exam.passed ? "" : " Now pass the readiness exam to get your certificate."}</p>}
        </div>
        <div className="setup-checks">
          <div className="tiny muted">YOUR SETUP (checked automatically)</div>
          {data.checks.map((c) => <div key={c.key} className={`small ${c.ok ? "pos" : "muted"}`}>{c.ok ? "✓" : "○"} {c.label}</div>)}
        </div>
      </div>
      <div className="academy-grid">
        <div className="stack">
          {data.courses.map((c) => {
            const isOpen = open === c.id || (open === null && data.next && c.lessons.some((x) => x.id === data.next.id));
            return (
              <div key={c.id} className={`course ${isOpen ? "open" : ""}`}>
                <button className="course-head" onClick={() => setOpen(isOpen ? "" : c.id)}>
                  <span className="course-icon">{c.icon}</span>
                  <span className="course-title"><b>{c.title}</b><span className="tiny muted">{c.summary}</span></span>
                  <span className="course-prog small">{c.done}/{c.total}<Progress done={c.done} total={c.total} /></span>
                </button>
                {isOpen && (
                  <div className="course-lessons">
                    {c.lessons.map((x) => (
                      <a key={x.id} href={`#/academy?l=${x.id}`} className={`lesson-row ${x.done ? "done" : ""}`}>
                        <span className="lr-dot">{x.done ? "✓" : x.started ? "◐" : "○"}</span>
                        <span className="lr-title">{x.title}</span>
                        {x.check && <span className={`tiny ${x.check_ok ? "pos" : "muted"}`} title="Checked automatically from your app">{x.check_ok ? "✓ set up" : "to do"}</span>}
                        <span className="tiny muted">{x.minutes} min</span>
                      </a>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
        <Tutor />
      </div>
    </div>
  );
}
