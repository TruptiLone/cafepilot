import { type FormEvent, useEffect, useRef, useState } from "react";
import { type ChatAnswer, AgentApiError, askMuse } from "../../api/agent";
import { formatQuantity } from "../agent/changes";
import { type SpeechRecognitionLike, canSpeak, speak, speechRecognition } from "./speech";
import { modelName } from "./story";

const SPEAK_KEY = "cafepilot-chat-speak";
export const ROW_LIMIT = 5;

export const EXAMPLES = [
  "Do we have banana nut muffins?",
  "Are we low on anything?",
  "Who's working Sunday morning?",
  "When does the milk arrive?",
];

type Turn = {
  id: number;
  question: string;
  channel: "text" | "voice";
  state: "loading" | "done" | "error";
  answer?: ChatAnswer;
  error?: { timeout: boolean; text: string };
};

function readPref(): boolean {
  try {
    return window.localStorage.getItem(SPEAK_KEY) === "1";
  } catch {
    return false;
  }
}

function writePref(on: boolean) {
  try {
    window.localStorage.setItem(SPEAK_KEY, on ? "1" : "0");
  } catch {
    /* per-viewer convenience only */
  }
}

/** The reply sentence(s): Spark's when its numbers checked out, else the code-written answer. */
export function replyText(answer: ChatAnswer): string {
  if (answer.intent === "needs_plan") return "That's a change, so it needs a plan you approve. Nothing changes from chat.";
  return answer.reply ?? answer.lines.join(" ");
}

function MuseIcon() {
  return (
    <span className="mz-a__icon" aria-hidden="true">
      <svg viewBox="0 0 24 24">
        <path d="M5 11h11v3.5a5 5 0 0 1-5 5h-1a5 5 0 0 1-5-5z" />
        <path d="M16 12h1.5a2.25 2.25 0 0 1 0 4.5H16" />
        <path d="M10.5 3v4.5M8.25 5.25h4.5" />
      </svg>
    </span>
  );
}

type ChatPanelProps = {
  /** Reference time for "today" (the demo event's knowledge time); the server uses now without it. */
  asOf?: string;
  onPlan: (request: string) => void;
};

function ChatPanel({ asOf, onPlan }: ChatPanelProps) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const [speakOn, setSpeakOn] = useState(readPref);
  const [listening, setListening] = useState(false);
  const recognition = useRef<SpeechRecognitionLike | null>(null);
  const heard = useRef("");
  const end = useRef<HTMLDivElement>(null);
  const busy = turns.some((t) => t.state === "loading");

  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end" });
  }, [turns]);

  async function ask(question: string, channel: "text" | "voice") {
    const q = question.trim();
    if (!q || busy) return;
    const id = Date.now();
    setTurns((t) => [...t, { id, question: q, channel, state: "loading" }]);
    setText("");
    try {
      const answer = await askMuse(q, channel, asOf);
      setTurns((t) => t.map((x) => (x.id === id ? { ...x, state: "done", answer } : x)));
      if (speakOn) speak(replyText(answer)); // the reply only, never the table
    } catch (error) {
      const timeout = error instanceof AgentApiError && error.status === 504;
      const message = error instanceof AgentApiError ? error.message : "Something went wrong. Try again.";
      setTurns((t) => t.map((x) => (x.id === id ? { ...x, state: "error", error: { timeout, text: message } } : x)));
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void ask(text, "text");
  }

  function toggleSpeak() {
    setSpeakOn((on) => {
      writePref(!on);
      if (on) window.speechSynthesis?.cancel();
      return !on;
    });
  }

  function toggleMic() {
    const Recognition = speechRecognition();
    if (!Recognition) return;
    if (listening) {
      recognition.current?.stop();
      return;
    }
    const r = new Recognition();
    r.lang = "en-US";
    r.interimResults = true;
    heard.current = "";
    r.onresult = (e) => {
      heard.current = Array.from(e.results)
        .map((result) => result[0]?.transcript ?? "")
        .join("");
      setText(heard.current);
    };
    r.onend = () => {
      setListening(false);
      if (heard.current.trim()) void ask(heard.current, "voice");
    };
    recognition.current = r;
    setListening(true);
    r.start();
  }

  return (
    <>
      <div className="mz-scroll mz-chat" aria-label="Chat with Muse">
        <div className="mz-chat__col">
          <div className="mz-chat__intro">
            <p className="mz-label">Ask Muse</p>
            <p className="mz-purpose">
              Questions about stock, sales, staff and deliveries. Muse looks it up; every number comes from the café's
              data. Changes go to a plan you approve.
            </p>
            <div className="mz-chat__ex" aria-label="Example questions">
              {EXAMPLES.map((q) => (
                <button key={q} type="button" className="mz-chip-btn" disabled={busy} onClick={() => ask(q, "text")}>
                  {q}
                </button>
              ))}
            </div>
          </div>

          {turns.map((turn) => (
            <div key={turn.id} className="mz-turn">
              <p className="mz-q">{turn.question}</p>
              <div className="mz-a__wrap">
                <MuseIcon />
                <Reply
                  turn={turn}
                  busy={busy}
                  onAsk={(q) => ask(q, "text")}
                  onPlan={onPlan}
                  onRetry={() => ask(turn.question, turn.channel)}
                />
              </div>
            </div>
          ))}
          <div ref={end} />
        </div>
      </div>

      <form className="mz-cmd" onSubmit={onSubmit}>
        <div className="mz-chat__col">
          <div className="mz-cmd__bar">
            <input
              type="text"
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Ask about stock, sales, staff or deliveries…"
              aria-label="Ask Muse a question"
              disabled={busy}
            />
            {speechRecognition() && (
              <button
                type="button"
                className={`mz-mic${listening ? " is-on" : ""}`}
                aria-label={listening ? "Stop listening" : "Ask by voice"}
                aria-pressed={listening}
                onClick={toggleMic}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <rect x="9" y="2" width="6" height="12" rx="3" />
                  <path d="M19 10v1a7 7 0 0 1-14 0v-1M12 18v4" />
                </svg>
              </button>
            )}
            {canSpeak() && (
              <button
                type="button"
                className={`mz-mic mz-speaker${speakOn ? " is-on" : ""}`}
                aria-label="Speak answers"
                title="Speak answers, spoken by your browser"
                aria-pressed={speakOn}
                onClick={toggleSpeak}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M4 9h4l5-4v14l-5-4H4z" />
                  {speakOn ? <path d="M16 8.5a5 5 0 0 1 0 7M18.5 6a8.5 8.5 0 0 1 0 12" /> : <path d="M17 9.5l5 5M22 9.5l-5 5" />}
                </svg>
              </button>
            )}
            <button type="submit" className="mz-send" aria-label="Ask" disabled={!text.trim() || busy}>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M12 19V5M5 12l7-7 7 7" />
              </svg>
            </button>
          </div>
          <div className="mz-audit">
            <i />
            read-only · nothing changes from chat
          </div>
        </div>
      </form>
    </>
  );
}

type ReplyProps = {
  turn: Turn;
  busy: boolean;
  onAsk: (question: string) => void;
  onPlan: (request: string) => void;
  onRetry: () => void;
};

function Reply({ turn, busy, onAsk, onPlan, onRetry }: ReplyProps) {
  const [all, setAll] = useState(false);
  if (turn.state === "loading")
    return (
      <div className="mz-a is-loading" role="status">
        Muse is looking it up…
      </div>
    );
  if (turn.state === "error" || !turn.answer)
    return (
      <div className="mz-a is-error" role="alert">
        <p>{turn.error?.timeout ? "Muse took more than 10 seconds to answer." : turn.error?.text}</p>
        <button type="button" className="mz-btn mz-btn--ghost" onClick={onRetry}>
          Try again
        </button>
      </div>
    );
  const a = turn.answer;
  const byCode = a.reply_by === "code" && a.intent === "answer";
  const meta = (
    <p className="mz-a__meta">
      {modelName(a.model)} · {(a.latency_ms / 1000).toFixed(1)} s{byCode ? " · answer written by code" : ""}
    </p>
  );
  if (a.intent === "needs_plan")
    return (
      <div className="mz-a is-plan">
        <p className="mz-a__reply">{replyText(a)}</p>
        <button type="button" className="mz-btn mz-btn--primary" onClick={() => onPlan(a.plan_request ?? turn.question)}>
          Plan it
        </button>
        {meta}
      </div>
    );
  // Always the first five rows; "Show all" only when there are more, so a question always looks the same.
  const rows = all ? a.rows : a.rows.slice(0, ROW_LIMIT);
  return (
    <div className={`mz-a${a.intent === "out_of_scope" ? " is-muted" : ""}`}>
      <p className="mz-a__reply">{replyText(a)}</p>
      {rows.length > 0 && (
        <table className="mz-a__rows">
          <tbody>
            {rows.map((r, i) => (
              <tr key={`${r.name}-${i}`}>
                <th scope="row">{r.name}</th>
                <td>{r.quantity !== null ? formatQuantity(r.quantity, r.unit) : ""}</td>
                <td className="mz-muted">{r.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {a.rows.length > ROW_LIMIT && !all && (
        <button type="button" className="mz-link mz-a__more" onClick={() => setAll(true)}>
          Show all {a.rows.length}
        </button>
      )}
      {a.notes?.map((note) => (
        <p key={note} className="mz-a__note">
          {note}
        </p>
      ))}
      {a.follow_ups && a.follow_ups.length > 0 && (
        <div className="mz-a__next" aria-label="Next steps">
          {a.follow_ups.map((f) => (
            <button
              key={f.label}
              type="button"
              className={`mz-chip-btn${f.kind === "plan" ? " is-plan" : ""}`}
              disabled={busy}
              onClick={() => (f.kind === "plan" ? onPlan(f.text) : onAsk(f.text))}
            >
              {f.label}
            </button>
          ))}
        </div>
      )}
      {meta}
    </div>
  );
}

export default ChatPanel;
