// A themed dot, not a raw colour literal repeated at every nav call site —
// one place decides what "unread, somewhere in the nav" looks like, shared
// by ManagerLayout and AgentLayout rather than a copy in each.
export function NavUnreadDot() {
  return (
    <span
      aria-label="Unread messages"
      className="absolute -right-0.5 -top-0.5 size-2 rounded-full bg-primary ring-2 ring-white"
    />
  );
}
