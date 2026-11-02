import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

const iconProps = {
  width: 24,
  height: 24,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

export function CoffeeIcon(props: IconProps) {
  return (
    <svg {...iconProps} {...props}>
      <path d="M5 8h11v7.2A3.8 3.8 0 0 1 12.2 19H8.8A3.8 3.8 0 0 1 5 15.2V8Z" />
      <path d="M16 10h1.3a2.7 2.7 0 0 1 0 5.4H16" />
      <path d="M8 5.2v-2M11 5.2v-2M14 5.2v-2" />
    </svg>
  );
}

export function DashboardIcon(props: IconProps) {
  return (
    <svg {...iconProps} {...props}>
      <rect x="4" y="4" width="6" height="6" rx="1" />
      <rect x="14" y="4" width="6" height="9" rx="1" />
      <rect x="4" y="14" width="6" height="6" rx="1" />
      <rect x="14" y="17" width="6" height="3" rx="1" />
    </svg>
  );
}

export function InventoryIcon(props: IconProps) {
  return (
    <svg {...iconProps} {...props}>
      <path d="m4.5 7.5 7.5-4 7.5 4v9l-7.5 4-7.5-4v-9Z" />
      <path d="m4.8 7.7 7.2 4 7.2-4M12 11.7v8.5" />
    </svg>
  );
}

export function SalesIcon(props: IconProps) {
  return (
    <svg {...iconProps} {...props}>
      <path d="M3.5 5h2l2.1 9.2h9.8l2-6.3H7" />
      <circle cx="9.2" cy="18.5" r="1.3" />
      <circle cx="17" cy="18.5" r="1.3" />
    </svg>
  );
}

export function StaffingIcon(props: IconProps) {
  return (
    <svg {...iconProps} {...props}>
      <circle cx="9" cy="8" r="3" />
      <circle cx="17" cy="9" r="2.5" />
      <path d="M3.8 19v-1.2A5.2 5.2 0 0 1 9 12.6a5.2 5.2 0 0 1 5.2 5.2V19" />
      <path d="M15.2 13.6a4.5 4.5 0 0 1 5 4.4v1" />
    </svg>
  );
}

export function MenuIcon(props: IconProps) {
  return (
    <svg {...iconProps} {...props}>
      <path d="M4 7h16M4 12h16M4 17h16" />
    </svg>
  );
}

export function CloseIcon(props: IconProps) {
  return (
    <svg {...iconProps} {...props}>
      <path d="m6 6 12 12M18 6 6 18" />
    </svg>
  );
}

export function AgentIcon(props: IconProps) {
  return (
    <svg {...iconProps} {...props}>
      <path d="M6 4h12v16l-2-1.5-2 1.5-2-1.5-2 1.5-2-1.5L6 20V4Z" />
      <path d="M9 9h6M9 12.5h6M9 16h3" />
    </svg>
  );
}

export function ReceivingIcon(props: IconProps) {
  return (
    <svg {...iconProps} {...props}>
      <path d="M3 7.5 12 3l9 4.5v9L12 21l-9-4.5v-9Z" />
      <path d="M3 7.5 12 12l9-4.5M12 12v9" />
      <path d="m8.5 15.5 1.6 1.6 3-3" />
    </svg>
  );
}
