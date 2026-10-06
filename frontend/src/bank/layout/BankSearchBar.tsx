// Command Center `components/SearchBar.jsx`, ported to TypeScript (spec §2.3).
// Class strings verbatim. Ctrl/Cmd+K focuses it, arrows move, Enter opens,
// Escape clears. The index is the built pages, whatever `extra` the caller
// passes, and — since /bank/customers/search exists — borrowers of the
// caller's own bank, which CC lists as "Account" hits.
//
// The lookup is the server's decision, never this component's: the bank comes
// from the caller's row, a region-limited user searches inside their region,
// and a borrower outside either is returned as nothing at all. So an empty list
// here never means "not yours" — it means nothing to show.
import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { useNavigate } from "react-router";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArrowRight, Command, Search, User, X, Zap } from "lucide-react";
import { searchCustomers } from "@/api/bankCustomer";
import { KIND_CHIP, PAGE_ENTRIES, searchEntries, type SearchEntry } from "./searchIndex";

/** Borrower hits shown beside the page hits, as CC shows accounts. */
const MAX_BORROWER_HITS = 4;
/** Keystrokes are not queries: the request waits for a pause in typing. */
const DEBOUNCE_MS = 220;

export function BankSearchBar({ extra = [] }: { extra?: SearchEntry[] }) {
  const [query, setQuery] = useState("");
  // The query as last SENT, which lags what is typed by one debounce.
  const [term, setTerm] = useState("");
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const debounce = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const navigate = useNavigate();

  const index = useMemo(() => [...PAGE_ENTRIES, ...extra], [extra]);
  // CC caps static hits at 5; borrowers add up to MAX_BORROWER_HITS more.
  const pages = searchEntries(index, query);

  // The server decides what is too short to search; 3 is only the gate that
  // keeps the box from asking on every second keystroke.
  const borrowers = useQuery({
    queryKey: ["bank", "customer-search", term],
    queryFn: () => searchCustomers(term),
    enabled: term.trim().length >= 3,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    retry: false,
  });

  const borrowerEntries: SearchEntry[] = useMemo(() => {
    if (!borrowers.data || borrowers.data.query_too_short) return [];
    return borrowers.data.items.slice(0, MAX_BORROWER_HITS).map((hit) => ({
      kind: "account" as const,
      id: `customer:${hit.customer_id}`,
      title: hit.full_name,
      subtitle: [hit.city, hit.first_account, hit.loans > 1 ? `${hit.loans} loans` : null]
        .filter(Boolean).join(" · "),
      path: `/bank/customers/${hit.customer_id}`,
      icon: User,
    }));
  }, [borrowers.data]);

  const results = useMemo(() => [...pages, ...borrowerEntries], [pages, borrowerEntries]);

  // Ctrl+K / Cmd+K focuses; Escape clears — window-wide, as in CC.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "k") {
        e.preventDefault();
        inputRef.current?.focus();
        setOpen(true);
      }
      if (e.key === "Escape") {
        setQuery("");
        setTerm("");
        setOpen(false);
        inputRef.current?.blur();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const handleSelect = (result: SearchEntry | undefined) => {
    if (!result) return;
    if (result.path) navigate(result.path);
    setQuery("");
    setTerm("");
    setOpen(false);
  };

  // Arrow-key navigation. CC listens on window while open; the input has
  // focus whenever the list is open, so its own onKeyDown is equivalent.
  const onKeyDown = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (!open || results.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setCursor((c) => Math.min(c + 1, results.length - 1));
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setCursor((c) => Math.max(c - 1, 0));
    }
    if (e.key === "Enter") {
      e.preventDefault();
      handleSelect(results[cursor]);
    }
  };

  const clear = () => {
    setQuery("");
    setTerm("");
    setCursor(0);
    inputRef.current?.focus();
  };

  return (
    <div ref={containerRef} className="relative w-80">
      {/* Input */}
      <div className={`flex min-h-10 items-center gap-2.5 bg-muted rounded-control px-3 transition-colors border ${open ? "border-primary ring-2 ring-primary/20" : "border-transparent hover:border-input"}`}>
        <Search size={14} className="text-muted-foreground shrink-0" />
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => {
            const next = e.target.value;
            setQuery(next);
            setCursor(0);
            setOpen(true);
            clearTimeout(debounce.current);
            debounce.current = setTimeout(() => setTerm(next), DEBOUNCE_MS);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          placeholder="Search pages and borrowers..."
          aria-label="Search"
          className="flex-1 text-[13px] bg-transparent outline-none text-foreground placeholder-muted-foreground min-w-0"
        />
        {query ? (
          <button onClick={clear} aria-label="Clear search" className="text-muted-foreground hover:text-foreground transition-colors shrink-0">
            <X size={13} />
          </button>
        ) : (
          <kbd className="hidden sm:flex items-center gap-0.5 text-[10px] text-muted-foreground bg-muted/50 border border-border/50 rounded px-1.5 py-0.5 font-semibold shrink-0">
            <Command size={9} />K
          </kbd>
        )}
      </div>

      {/* Dropdown */}
      {open && (
        <div className="absolute top-full left-0 right-0 mt-1.5 bg-card border border-border rounded-inner shadow-menu overflow-hidden z-50 animate-slide-up">
          {results.length === 0 && query.trim().length > 0 && (
            <div className="px-4 py-6 text-center">
              <p className="text-[13px] text-muted-foreground">
                No results for <span className="font-semibold text-foreground">"{query}"</span>
              </p>
            </div>
          )}

          {results.length === 0 && query.trim().length === 0 && (
            <div className="px-4 py-3">
              <p className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider mb-2">Quick Access</p>
              <div className="space-y-0.5">
                {index
                  .filter((r) => r.kind === "tool" || r.kind === "page")
                  .slice(0, 4)
                  .map((r) => {
                    const meta = KIND_CHIP[r.kind];
                    const Icon = r.icon ?? Zap;
                    return (
                      <button
                        key={r.id}
                        onMouseDown={() => handleSelect(r)}
                        className="w-full flex items-center gap-3 px-2 py-2 rounded-lg hover:bg-muted/40 text-left transition-colors group"
                      >
                        <span className={`w-6 h-6 rounded-md flex items-center justify-center shrink-0 ${meta.bg}`}>
                          <Icon size={11} className={meta.color} />
                        </span>
                        <div className="min-w-0 flex-1">
                          <p className="text-[12px] font-medium text-foreground truncate">{r.title}</p>
                        </div>
                        <ArrowRight size={11} className="text-muted-foreground/40 group-hover:text-primary shrink-0 transition-colors" />
                      </button>
                    );
                  })}
              </div>
            </div>
          )}

          {results.length > 0 && (
            <div className="py-1.5">
              {results.map((r, i) => {
                const meta = KIND_CHIP[r.kind];
                const Icon = r.icon ?? Zap;
                const isActive = i === cursor;
                return (
                  <button
                    key={r.id}
                    onMouseDown={() => handleSelect(r)}
                    onMouseEnter={() => setCursor(i)}
                    className={`w-full flex items-center gap-3 px-3 py-2.5 text-left transition-colors ${isActive ? "bg-primary/5" : "hover:bg-muted/30"}`}
                  >
                    <span className={`w-7 h-7 rounded-lg flex items-center justify-center shrink-0 ${meta.bg}`}>
                      <Icon size={12} className={meta.color} />
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="text-[13px] font-medium text-foreground truncate">{r.title}</p>
                      <p className="text-[11px] text-muted-foreground truncate mt-0.5">{r.subtitle}</p>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full border ${meta.bg} ${meta.color} border-current/20`}>
                        {meta.label}
                      </span>
                      {r.kind === "tool" && (
                        <ArrowRight size={11} className={`transition-colors ${isActive ? "text-primary" : "text-muted-foreground/40"}`} />
                      )}
                    </div>
                  </button>
                );
              })}
            </div>
          )}

          {results.length > 0 && (
            <div className="px-3 py-2 border-t border-white/20 flex items-center gap-3 text-[10px] text-muted-foreground bg-white/30">
              <span className="flex items-center gap-1"><kbd className="bg-muted border border-border/50 rounded px-1 font-mono">↑↓</kbd> navigate</span>
              <span className="flex items-center gap-1"><kbd className="bg-muted border border-border/50 rounded px-1 font-mono">↵</kbd> open</span>
              <span className="flex items-center gap-1"><kbd className="bg-muted border border-border/50 rounded px-1 font-mono">Esc</kbd> close</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
