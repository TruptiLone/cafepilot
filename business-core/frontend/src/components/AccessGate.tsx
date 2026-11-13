import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { AgentApiError } from "../api/agent";
import { enterAccessCode, getAccess, LOCKED_EVENT } from "../api/demo";
import Brand from "./Brand";

type Gate = "checking" | "open" | "locked";

/** The public demo asks for an access code before anything else loads. Locally (no code set) it just opens. */
function AccessGate({ children }: { children: ReactNode }) {
  const [gate, setGate] = useState<Gate>("checking");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  // Bumped when the visitor gets a fresh café, so every page loads again from it.
  const [visit, setVisit] = useState(0);

  useEffect(() => {
    // A 503 means the demo is full or not ready: say so on the gate. Anything else (an older API without the
    // gate): open, and let the pages report their own errors.
    const failed = (e: unknown) => {
      if (e instanceof AgentApiError && e.status === 503) {
        setError(e.message);
        setGate("locked");
      } else {
        setGate("open");
      }
    };
    getAccess()
      .then((status) => setGate(!status.required || status.granted ? "open" : "locked"))
      .catch(failed);
    // An API call came back locked: the code expired, or the visitor's café was closed after sitting unused.
    // Asking again makes a fresh café when the code is still good; only otherwise does the code page come back.
    let checking = false;
    const reopened: number[] = [];
    const relock = () => {
      if (checking) return;
      checking = true;
      getAccess()
        .then((status) => {
          const now = Date.now();
          while (reopened.length && now - reopened[0] > 30_000) reopened.shift();
          if ((!status.required || status.granted) && reopened.length < 3) {
            reopened.push(now); // at most 3 fresh starts in 30 s, so a broken café can't loop
            setVisit((n) => n + 1);
            setGate("open");
          } else {
            setGate("locked");
          }
        })
        .catch((e: unknown) => {
          if (e instanceof Error) setError(e.message);
          setGate("locked");
        })
        .finally(() => {
          checking = false;
        });
    };
    window.addEventListener(LOCKED_EVENT, relock);
    return () => window.removeEventListener(LOCKED_EVENT, relock);
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!code.trim()) return;
    setSending(true);
    setError(null);
    try {
      await enterAccessCode(code.trim());
      setGate("open");
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't work. Try again.");
    } finally {
      setSending(false);
    }
  }

  if (gate === "open") return <div key={visit} style={{ display: "contents" }}>{children}</div>;
  if (gate === "checking") return <div className="gate" aria-busy="true" />;

  return (
    <main className="gate">
      <form className="gate__card" onSubmit={submit}>
        <Brand compact />
        <h1 className="gate__title">Welcome to the CaféPilot demo</h1>
        <p className="gate__text">
          An AI operations assistant for a small café, powered by Meta's Muse models. Enter the access code you were
          given to try it.
        </p>
        <label className="gate__label" htmlFor="access-code">
          Access code
        </label>
        <input
          id="access-code"
          className="gate__input"
          type="password"
          autoComplete="off"
          autoFocus
          value={code}
          onChange={(event) => setCode(event.target.value)}
        />
        {error && (
          <p className="gate__error" role="alert">
            {error}
          </p>
        )}
        <button className="gate__button" type="submit" disabled={sending || !code.trim()}>
          {sending ? "Checking…" : "Open the demo"}
        </button>
        <p className="gate__note">All data is made up. Nothing is ordered or sent for real.</p>
      </form>
    </main>
  );
}

export default AccessGate;
