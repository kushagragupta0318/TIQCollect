// The six-step stepper shell. No such primitive exists in bank/ui yet (the
// brief calls this out explicitly), so it is built here from the same tokens
// every other bank primitive uses — rounded-control/rounded-card radii,
// border/muted/primary/success colours off bank.css's variables — rather than
// inventing a new visual language for one page.
import { Check } from "lucide-react";
import { cn } from "../../lib/cn";

export interface WizardStepDef {
  id: number;
  label: string;
  /** Shown once the step has something saved worth a checkmark. */
  complete: boolean;
}

interface WizardStepperProps {
  steps: WizardStepDef[];
  active: number;
  /** Steps other than 1 are unreachable until a draft exists. */
  locked: boolean;
  onSelect: (id: number) => void;
}

export function WizardStepper({ steps, active, locked, onSelect }: WizardStepperProps) {
  return (
    <ol className="flex flex-wrap items-stretch gap-2" aria-label="Onboarding steps">
      {steps.map((step) => {
        const isActive = step.id === active;
        const isLocked = locked && step.id !== 1;
        return (
          <li key={step.id} className="flex-1 min-w-[140px]">
            <button
              type="button"
              disabled={isLocked}
              aria-current={isActive ? "step" : undefined}
              onClick={() => onSelect(step.id)}
              className={cn(
                "flex w-full items-center gap-2.5 rounded-control border px-3 py-2.5 text-left transition-colors",
                isActive
                  ? "border-primary bg-accent text-accent-foreground"
                  : "border-border bg-card text-foreground hover:bg-muted",
                isLocked && "cursor-not-allowed opacity-50 hover:bg-card",
              )}
            >
              <span
                className={cn(
                  "flex size-6 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold",
                  step.complete
                    ? "bg-success text-success-foreground"
                    : isActive
                      ? "bg-primary text-primary-foreground"
                      : "bg-muted text-muted-foreground",
                )}
              >
                {step.complete ? <Check size={13} /> : step.id}
              </span>
              <span className="min-w-0">
                <span className="block truncate text-[12.5px] font-semibold leading-tight">{step.label}</span>
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
