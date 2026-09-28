// Command Center `components/SearchBar.jsx`, ported to TypeScript (spec §2.3).
// Class strings verbatim. Ctrl/Cmd+K focuses it, arrows move, Enter opens,
// Escape clears. Account lookup is not wired: the bank has no account-search
// endpoint yet, so the index is pages plus whatever `extra` the caller passes.
import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { useNavigate } from "react-router";
import { ArrowRight, Command, Search, X, Zap } from "lucide-react";
import { KIND_CHIP, PAGE_ENTRIES, searchEntries, type SearchEntry } from "./searchIndex";

export function BankSearchBar({ extra = [] }: { extra?: SearchEntry[] }) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  const index = useMemo(() => [...PAGE_ENTRIES, ...extra], [extra]);
  // CC caps static hits at 5 (accounts would add up to 4 more; none here yet).
  const results = searchEntries(index, query);

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
            setQuery(e.target.value);
            setCursor(0);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          placeholder="Search pages, KPIs, alerts..."
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
