import type { AgentRun } from "../../api/agent";

/** Roles that can make espresso (café policy CAFE001). Display only: decisions are checked in code. */
const ESPRESSO_ROLES: Record<string, string[]> = { CAFE001: ["barista", "shift_lead"] };

export function policyRoles(run: AgentRun): string[] {
  return ESPRESSO_ROLES[run.cafe_id] ?? [];
}
