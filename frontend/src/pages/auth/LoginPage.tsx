import { useEffect, useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router";
import { Eye, EyeOff, ShieldCheck } from "lucide-react";
import { toast } from "react-hot-toast";
import { login as apiLogin } from "@/api/auth";
import { useAuthStore } from "@/store/authStore";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";

export default function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { setTokens, setUser, deviceId } = useAuthStore();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const prefill = (location.state as { prefill?: string } | null)?.prefill;
    if (prefill === "agent") quickLogin("agent");
    else if (prefill === "manager") quickLogin("manager");
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!email || !password) return;
    setLoading(true);
    try {
      const data = await apiLogin(email, password, deviceId);
      setTokens(data.access_token, data.refresh_token);
      setUser({ id: data.user_id, email, full_name: data.full_name, role: data.role, is_active: true });
      toast.success(`Welcome, ${data.full_name.split(" ")[0]}!`);
      navigate(data.role === "FIELD_AGENT" ? "/agent/home" : "/manager/overview", { replace: true });
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(msg ?? "Invalid email or password.");
    } finally {
      setLoading(false);
    }
  }

  function quickLogin(type: "agent" | "manager") {
    setEmail(type === "agent" ? "agent002@tiqcollect.in" : "manager1@tiqcollect.in");
    setPassword(type === "agent" ? "Agent@123" : "Manager@123");
  }

  return (
    <main className="login-page fixed inset-0 overflow-hidden bg-[#EEF0F4] p-2 font-sans sm:p-3">
      <section className="mx-auto grid h-full max-h-full w-full max-w-[1680px] grid-cols-1 gap-3 overflow-hidden rounded-card border border-white bg-white p-3 shadow-[0_8px_32px_rgba(16,24,40,0.08)] lg:grid-cols-2">
        <div className="flex min-h-0 min-w-0 flex-col overflow-hidden px-4 py-3 sm:px-8 lg:px-8 xl:px-10">
          <button
            type="button"
            onClick={() => navigate("/")}
            className="inline-flex min-h-10 w-fit items-center gap-3 rounded-control text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/25 focus-visible:ring-offset-2"
            aria-label="Go to TIQCollect homepage"
          >
            <span className="flex size-9 items-center justify-center rounded-control bg-brand-50 text-brand-600">
              <ShieldCheck className="size-5" />
            </span>
            <span className="text-lg font-bold tracking-[-0.02em] text-[#101828]">TIQCollect</span>
          </button>

          <div className="flex min-h-0 flex-1 items-center justify-center overflow-hidden py-2">
            <div className="w-full max-w-md">
              <header className="mb-5 text-center">
                <h1 className="text-[36px] font-extrabold tracking-[-0.045em] text-[#101828] sm:text-[40px]">
                  Welcome Back
                </h1>
                <p className="mt-2 text-sm font-normal text-[#98A2B3]">
                  Enter your email and password to access your account.
                </p>
              </header>

              <form onSubmit={handleSubmit} className="space-y-3">
                <Input
                  label="Email"
                  type="email"
                  placeholder="you@tiqcollect.in"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  autoComplete="email"
                  className="h-10 px-3"
                  required
                />

                <div className="w-full">
                  <label className="mb-1.5 block text-[13px] font-normal text-[#98A2B3]">Password</label>
                  <div className="relative">
                    <input
                      type={showPassword ? "text" : "password"}
                      className="input h-10 px-3 pr-11"
                      placeholder="Enter your password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      autoComplete="current-password"
                      required
                    />
                    <button
                      type="button"
                      aria-label={showPassword ? "Hide password" : "Show password"}
                      className="absolute right-1 top-1/2 flex size-9 -translate-y-1/2 items-center justify-center rounded-control text-slate-400 hover:bg-brand-50 hover:text-brand-600"
                      onClick={() => setShowPassword((v) => !v)}
                    >
                      {showPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                    </button>
                  </div>
                </div>

                <div className="flex items-center justify-between gap-4 text-xs">
                  <label className="flex cursor-pointer items-center gap-2 !text-[#667085]">
                    <input type="checkbox" className="size-4 rounded border-[#D0D5DD] text-primary focus:ring-primary/20" />
                    Remember me
                  </label>
                  <button
                    type="button"
                    onClick={() => toast("Password recovery is managed by your administrator.")}
                    className="min-h-9 font-medium text-brand-600 hover:text-brand-700"
                  >
                    Forgot your password?
                  </button>
                </div>

                <Button
                  type="submit"
                  fullWidth
                  loading={loading}
                  size="md"
                  className="!border-brand-600 !bg-brand-600 !text-white hover:!border-brand-700 hover:!bg-brand-700"
                >
                  Sign In
                </Button>
              </form>

              <div className="my-4 flex items-center gap-3 py-1">
                <span className="h-px flex-1 bg-[#ECEDF1]" />
                <span className="px-2 text-xs leading-5 text-[#98A2B3]">Quick demo login</span>
                <span className="h-px flex-1 bg-[#ECEDF1]" />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <button
                  type="button"
                  onClick={() => quickLogin("agent")}
                  className="min-h-10 rounded-control border border-[#BFDBFE] bg-[#EFF6FF] px-3 text-sm font-medium text-[#1D4ED8] transition-colors hover:bg-[#DBEAFE] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/20"
                >
                  Field Agent
                </button>
                <button
                  type="button"
                  onClick={() => quickLogin("manager")}
                  className="min-h-10 rounded-control border border-[#DDD6FE] bg-[#F5F3FF] px-3 text-sm font-medium text-[#6D28D9] transition-colors hover:bg-[#EDE9FE] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500/20"
                >
                  Manager
                </button>
              </div>

              <p className="mt-3 text-center text-xs text-[#98A2B3] [@media(max-height:600px)]:hidden">
                Access is assigned by your organization administrator.
              </p>
            </div>
          </div>

          <footer className="flex flex-wrap items-center justify-between gap-3 pt-2 text-xs text-[#98A2B3] [@media(max-height:600px)]:hidden">
            <span>Copyright © 2025 TIQCollect</span>
            <span>RBI Compliant</span>
          </footer>
        </div>

        <aside className="relative hidden h-full min-h-0 overflow-hidden rounded-card bg-brand-600 text-white lg:flex lg:flex-col">
          <div className="pointer-events-none absolute inset-0" aria-hidden="true">
            <div className="absolute -right-20 -top-24 size-[390px] rounded-full bg-white/[0.035]" />
            <div className="absolute left-0 top-0 h-full w-[13%] bg-blue-700/25" />
            <div className="absolute right-[9%] top-0 h-full w-[7%] bg-blue-700/20" />
            <div className="absolute bottom-0 left-[48%] h-[28%] w-[38%] border border-white/[0.055]" />
            <div className="absolute bottom-[-90px] left-[-42px] size-[280px] rounded-full bg-blue-500/25" />
            <div className="absolute inset-x-0 top-[54%] h-px bg-white/[0.06]" />
          </div>

          <div className="relative z-10 flex h-full min-h-0 flex-col overflow-hidden px-8 py-6 xl:px-10 xl:py-8">
            <div className="mx-auto flex h-full min-h-0 w-full max-w-[500px] flex-col overflow-hidden pt-[clamp(0.75rem,4svh,3rem)]">
              <h2
                className="max-w-full text-left text-[38px] font-extrabold leading-[1.16] tracking-[-0.045em] text-white xl:text-[44px]"
                aria-label="Effortlessly manage field collections and recovery."
              >
                <span className="login-type-line login-type-heading-1">Effortlessly manage</span>
                <span className="login-type-line login-type-heading-2">field collections</span>
                <span className="login-type-line login-type-heading-3">and recovery.</span>
              </h2>
              <p
                className="mt-6 max-w-full text-left text-base font-medium leading-6 text-white/80"
                aria-label="Access live cases, agent activity, and recovery performance from one workspace."
              >
                <span className="login-type-line login-type-copy-1">Access live cases, agent activity,</span>
                <span className="login-type-line login-type-copy-2">and recovery performance.</span>
              </p>

              <div className="login-showcase relative mt-[clamp(1rem,2.5svh,1.75rem)] min-h-0 flex-1">
                <div className="login-asset login-asset-dashboard absolute left-0 top-0 w-[72%] max-w-[390px] overflow-hidden rounded-[18px] border border-white/70 bg-white p-1.5 shadow-[0_24px_55px_rgba(20,26,92,0.28)]">
                  <img
                    src="/assets/Screenshot%202026-08-12%20131429.png"
                    alt="Field operations manager dashboard overview"
                    width="1277"
                    height="935"
                    className="block h-auto w-full rounded-[13px]"
                    loading="eager"
                    decoding="async"
                  />
                </div>

                <div className="login-asset login-asset-dashboard-secondary absolute bottom-[clamp(0.25rem,1svh,0.75rem)] right-0 z-10 w-[72%] max-w-[390px] overflow-hidden rounded-[16px] border border-white/80 bg-white p-1.5 shadow-[0_22px_48px_rgba(20,26,92,0.32)]">
                  <img
                    src="/assets/Screenshot%202026-08-12%20131550.png"
                    alt="Field collections analytics dashboard"
                    width="1277"
                    height="937"
                    className="block h-auto w-full rounded-[11px]"
                    loading="eager"
                    decoding="async"
                  />
                </div>
              </div>
            </div>
          </div>
        </aside>
      </section>
    </main>
  );
}
