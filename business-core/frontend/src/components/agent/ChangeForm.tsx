import { type FormEvent, useState } from "react";
import type { AgentRun, RunStep } from "../../api/agent";
import { formatQuantity, ingredientName, resultsOf } from "./changes";

type ChangeFormProps = {
  step: RunStep;
  run: AgentRun;
  busy: boolean;
  onSubmit: (editedArgs: Record<string, unknown>) => void;
  onCancel: () => void;
};

type Row = Record<string, unknown>;

export function canChange(step: RunStep): boolean {
  return step.action === "propose_supplier_order" || step.action === "schedule_shift";
}

/** "2026-11-01T07:30:00-08:00" -> { date, time: "07:30", offset: "-08:00" } */
function splitTimestamp(value: unknown) {
  const text = String(value ?? "");
  return { date: text.slice(0, 10), time: text.slice(11, 16), offset: text.slice(19) || "Z" };
}

function ChangeForm({ step, run, busy, onSubmit, onCancel }: ChangeFormProps) {
  const args = (step.resolved_args ?? step.args) as Row;
  return step.action === "propose_supplier_order" ? (
    <OrderChange args={args} run={run} busy={busy} onSubmit={onSubmit} onCancel={onCancel} />
  ) : (
    <ShiftChange args={args} run={run} busy={busy} onSubmit={onSubmit} onCancel={onCancel} />
  );
}

type FormProps = Omit<ChangeFormProps, "step"> & { args: Row };

function OrderChange({ args, run, busy, onSubmit, onCancel }: FormProps) {
  const lines = (Array.isArray(args.lines) ? args.lines : []) as Row[];
  const [quantities, setQuantities] = useState(lines.map((line) => String(line.quantity ?? "")));

  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit({
      lines: lines.map((line, index) => ({ ...line, quantity: quantities[index].trim() })),
    });
  }

  return (
    <form className="change-form" onSubmit={submit} aria-label="Change the order">
      {lines.map((line, index) => {
        const id = `qty-${String(line.ingredient_id)}`;
        return (
          <div className="change-form__row" key={id}>
            <label htmlFor={id}>{ingredientName(run, String(line.ingredient_id))}</label>
            <input
              id={id}
              type="number"
              min="0"
              step="any"
              inputMode="decimal"
              value={quantities[index]}
              onChange={(event) =>
                setQuantities((current) => current.map((value, i) => (i === index ? event.target.value : value)))
              }
            />
            <span className="change-form__unit">
              {String(line.unit)}
              {quantities[index] && ` (${formatQuantity(quantities[index], line.unit)})`}
            </span>
          </div>
        );
      })}
      <FormActions busy={busy} onCancel={onCancel} />
    </form>
  );
}

function ShiftChange({ args, run, busy, onSubmit, onCancel }: FormProps) {
  const start = splitTimestamp(args.start_at);
  const end = splitTimestamp(args.end_at);
  const staff = resultsOf(run, "list_employees");
  const [employeeId, setEmployeeId] = useState(String(args.employee_id ?? ""));
  const [startTime, setStartTime] = useState(start.time);
  const [endTime, setEndTime] = useState(end.time);

  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit({
      employee_id: employeeId,
      start_at: `${start.date}T${startTime}:00${start.offset}`,
      end_at: `${end.date}T${endTime}:00${end.offset}`,
    });
  }

  return (
    <form className="change-form" onSubmit={submit} aria-label="Change the shift">
      <div className="change-form__row">
        <label htmlFor="shift-person">Who</label>
        <select id="shift-person" value={employeeId} onChange={(event) => setEmployeeId(event.target.value)}>
          {staff.length === 0 && <option value={employeeId}>{employeeId}</option>}
          {staff.map((person) => (
            <option key={String(person.employee_id)} value={String(person.employee_id)}>
              {String(person.name)} ({String(person.role).replaceAll("_", " ")})
            </option>
          ))}
        </select>
      </div>
      <div className="change-form__row">
        <label htmlFor="shift-start">Starts</label>
        <input id="shift-start" type="time" value={startTime} onChange={(event) => setStartTime(event.target.value)} />
      </div>
      <div className="change-form__row">
        <label htmlFor="shift-end">Ends</label>
        <input id="shift-end" type="time" value={endTime} onChange={(event) => setEndTime(event.target.value)} />
      </div>
      <FormActions busy={busy} onCancel={onCancel} />
    </form>
  );
}

function FormActions({ busy, onCancel }: { busy: boolean; onCancel: () => void }) {
  return (
    <div className="change-form__actions">
      <button type="submit" className="primary-button" disabled={busy}>
        {busy ? "Approving…" : "Approve with changes"}
      </button>
      <button type="button" className="secondary-button" disabled={busy} onClick={onCancel}>
        Cancel
      </button>
    </div>
  );
}

export default ChangeForm;
