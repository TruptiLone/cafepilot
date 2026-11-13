import { useState } from "react";
import { startOver } from "../api/demo";

const QUESTION =
  "Start the demo over? Plans, orders, shifts and delivery checks go back to Thursday, Nov 5, 6 PM. This can't be undone.";

/** "Start over": puts the demo café back to the beginning, then reloads on Ask CaféPilot. */
function StartOver({ reload = () => window.location.assign("/agent") }: { reload?: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function click() {
    if (!window.confirm(QUESTION)) return;
    setBusy(true);
    setError(null);
    try {
      await startOver();
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't start over. Try again.");
      setBusy(false);
    }
  }

  return (
    <div className="start-over">
      <button className="start-over__button" type="button" disabled={busy} onClick={click}>
        {busy ? "Starting over…" : "Start the demo over"}
      </button>
      {error && (
        <span className="start-over__error" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

export default StartOver;
