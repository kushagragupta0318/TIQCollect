import { useNavigate } from "react-router";
import {
  ShieldCheck, Map, Zap, Brain, BarChart3, FileCheck, CheckCircle,
  ArrowRight, Phone, Navigation, Bell, Lock, Star, Users, IndianRupee, TrendingUp,
  ChevronRight, Globe, Clock, AlertTriangle
} from "lucide-react";

export default function LandingPage() {
  const navigate = useNavigate();

  function goLogin(type?: "agent" | "manager") {
    if (type === "agent") {
      // Pre-fill agent demo creds via URL state
      navigate("/login", { state: { prefill: "agent" } });
    } else if (type === "manager") {
      navigate("/login", { state: { prefill: "manager" } });
    } else {
      navigate("/login");
    }
  }

  return (
    <div className="min-h-screen bg-white font-sans">
      {/* ─── Navbar ─── */}
      <nav className="sticky top-0 z-50 bg-white/95 backdrop-blur border-b border-slate-100 shadow-sm">
        <div className="max-w-7xl mx-auto px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 bg-brand-600 rounded-lg flex items-center justify-center">
              <ShieldCheck className="w-5 h-5 text-white" />
            </div>
            <span className="text-xl font-bold text-slate-900 tracking-tight">TIQCollect</span>
            <span className="hidden sm:inline-block text-xs font-medium bg-brand-50 text-brand-600 border border-brand-100 px-2 py-0.5 rounded-full ml-1">Enterprise</span>
          </div>

          <div className="hidden lg:flex items-center gap-8 text-sm font-medium text-slate-600">
            <a href="#features" className="hover:text-brand-600 transition-colors">Features</a>
            <a href="#how-it-works" className="hover:text-brand-600 transition-colors">How It Works</a>
            <a href="#compliance" className="hover:text-brand-600 transition-colors">Compliance</a>
            <a href="#impact" className="hover:text-brand-600 transition-colors">Impact</a>
          </div>

          <div className="flex items-center gap-3">
            <button onClick={() => goLogin()} className="text-sm font-medium text-slate-600 hover:text-slate-900 transition-colors px-3 py-2">
              Log In
            </button>
            <button onClick={() => goLogin("manager")} className="hidden sm:flex items-center gap-1.5 bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2 rounded-lg transition-colors shadow-sm">
              Get Started <ArrowRight className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </nav>

      {/* ─── Hero ─── */}
      <section className="relative overflow-hidden bg-gradient-to-br from-slate-900 via-brand-900 to-brand-800 text-white">
        {/* Background grid */}
        <div className="absolute inset-0 opacity-10" style={{ backgroundImage: "radial-gradient(circle at 1px 1px, rgba(255,255,255,0.15) 1px, transparent 0)", backgroundSize: "32px 32px" }} />

        <div className="max-w-7xl mx-auto px-6 lg:px-8 py-20 lg:py-28">
          <div className="grid lg:grid-cols-2 gap-12 items-center">
            {/* Left — copy */}
            <div>
              <div className="inline-flex items-center gap-2 bg-brand-500/20 border border-brand-400/30 rounded-full px-4 py-1.5 text-sm font-medium text-brand-200 mb-6">
                <Zap className="w-3.5 h-3.5" /> AI-Powered Collections Platform
              </div>
              <h1 className="text-4xl lg:text-5xl xl:text-6xl font-extrabold leading-tight tracking-tight text-white mb-6">
                Collections that
                <span className="block text-transparent bg-clip-text bg-gradient-to-r from-brand-300 to-cyan-300">
                  actually collect.
                </span>
              </h1>
              <p className="text-lg text-brand-100 leading-relaxed mb-8 max-w-lg">
                Enterprise-grade field collections management for Indian NBFCs & banks. ML-optimised case allocation, real-time agent tracking, RBI-compliant workflows — all in one platform.
              </p>

              <div className="flex flex-wrap gap-3 mb-10">
                <button onClick={() => goLogin("manager")} className="flex items-center gap-2 bg-white text-brand-700 font-bold px-6 py-3 rounded-xl hover:bg-brand-50 transition-colors shadow-lg text-sm">
                  <BarChart3 className="w-4 h-4" /> Manager Dashboard
                </button>
                <button onClick={() => goLogin("agent")} className="flex items-center gap-2 bg-brand-500/30 border border-brand-400/50 text-white font-semibold px-6 py-3 rounded-xl hover:bg-brand-500/40 transition-colors text-sm">
                  <Phone className="w-4 h-4" /> Field Agent App
                </button>
              </div>

              <div className="flex flex-wrap gap-5 text-sm text-brand-200">
                {["No setup required", "RBI Compliant", "Works offline"].map((t) => (
                  <span key={t} className="flex items-center gap-1.5"><CheckCircle className="w-4 h-4 text-brand-300" />{t}</span>
                ))}
              </div>
            </div>

            {/* Right — dashboard preview card */}
            <div className="relative hidden lg:block">
              <div className="bg-white/10 backdrop-blur border border-white/20 rounded-2xl p-4 shadow-2xl">
                {/* Mock dashboard header */}
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <p className="text-xs text-brand-200">Agency Dashboard</p>
                    <p className="font-bold text-white">Today's Overview</p>
                  </div>
                  <div className="flex items-center gap-1.5 text-xs bg-success-500/20 text-success-300 px-2.5 py-1 rounded-full border border-success-400/30">
                    <span className="w-1.5 h-1.5 rounded-full bg-success-400 animate-pulse" />
                    Live
                  </div>
                </div>
                {/* KPI tiles */}
                <div className="grid grid-cols-2 gap-2 mb-4">
                  {[
                    { label: "Agents On Duty", value: "32/50", icon: "👥", color: "text-blue-300" },
                    { label: "Collected Today", value: "₹28.5L", icon: "💰", color: "text-green-300" },
                    { label: "Cases Resolved", value: "87", icon: "✅", color: "text-green-300" },
                    { label: "Collection Rate", value: "52.8%", icon: "📈", color: "text-yellow-300" },
                  ].map((k) => (
                    <div key={k.label} className="bg-white/10 rounded-xl p-3 border border-white/10">
                      <p className="text-lg">{k.icon}</p>
                      <p className={`text-base font-bold ${k.color}`}>{k.value}</p>
                      <p className="text-xs text-white/60 mt-0.5">{k.label}</p>
                    </div>
                  ))}
                </div>
                {/* Progress bar */}
                <div className="bg-white/10 rounded-xl p-3 border border-white/10">
                  <div className="flex justify-between text-xs text-white/70 mb-1.5">
                    <span>Collection Target Progress</span>
                    <span className="font-semibold text-green-300">52.8%</span>
                  </div>
                  <div className="w-full bg-white/20 rounded-full h-2">
                    <div className="h-2 rounded-full bg-gradient-to-r from-green-400 to-emerald-500" style={{ width: "52.8%" }} />
                  </div>
                  <p className="text-xs text-white/50 mt-1.5">₹28.5L of ₹54L target · ₹25.5L remaining</p>
                </div>
              </div>

              {/* Floating alert card */}
              <div className="absolute -bottom-4 -left-6 bg-white rounded-2xl shadow-xl border border-slate-100 p-3 w-52">
                <div className="flex items-center gap-2.5">
                  <div className="w-8 h-8 bg-success-100 rounded-lg flex items-center justify-center flex-shrink-0">
                    <CheckCircle className="w-4 h-4 text-success-600" />
                  </div>
                  <div>
                    <p className="text-xs font-semibold text-slate-900">Payment Collected</p>
                    <p className="text-xs text-slate-400">₹48,500 · Rajesh Kumar</p>
                  </div>
                </div>
              </div>

              {/* Floating agent card */}
              <div className="absolute -top-4 -right-4 bg-white rounded-2xl shadow-xl border border-slate-100 p-3 w-44">
                <div className="flex items-center gap-2 mb-1.5">
                  <div className="w-6 h-6 bg-brand-100 rounded-full flex items-center justify-center text-brand-700 text-xs font-bold">P</div>
                  <p className="text-xs font-semibold text-slate-900">Priya Sharma</p>
                </div>
                <div className="flex items-center gap-1.5 text-xs text-slate-500">
                  <Navigation className="w-3 h-3 text-brand-500" />
                  <span>Beat #3 · Stop 5/12</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ─── Stats bar ─── */}
      <section className="bg-brand-600 text-white">
        <div className="max-w-7xl mx-auto px-6 lg:px-8 py-10">
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-8 text-center">
            {[
              { value: "3.2×", label: "Higher collection rate vs manual" },
              { value: "₹2.8L", label: "Avg daily collection per agency" },
              { value: "94%", label: "RBI compliance score" },
              { value: "18min", label: "Avg case resolution time" },
            ].map((s) => (
              <div key={s.label}>
                <p className="text-3xl lg:text-4xl font-extrabold text-white">{s.value}</p>
                <p className="text-sm text-brand-200 mt-1">{s.label}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ─── Features ─── */}
      <section id="features" className="py-20 lg:py-28 bg-slate-50">
        <div className="max-w-7xl mx-auto px-6 lg:px-8">
          <div className="text-center mb-14">
            <span className="text-sm font-semibold text-brand-600 uppercase tracking-wider">Platform Features</span>
            <h2 className="text-3xl lg:text-4xl font-bold text-slate-900 mt-2">Everything your collections team needs</h2>
            <p className="text-slate-500 mt-3 max-w-xl mx-auto">From ML-driven overnight allocation to real-time GPS tracking and RBI compliance — purpose-built for Indian debt collections.</p>
          </div>

          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
            {FEATURES.map((f) => (
              <FeatureCard key={f.title} {...f} />
            ))}
          </div>
        </div>
      </section>

      {/* ─── How It Works ─── */}
      <section id="how-it-works" className="py-20 lg:py-28 bg-white">
        <div className="max-w-7xl mx-auto px-6 lg:px-8">
          <div className="text-center mb-14">
            <span className="text-sm font-semibold text-brand-600 uppercase tracking-wider">Workflow</span>
            <h2 className="text-3xl lg:text-4xl font-bold text-slate-900 mt-2">How TIQCollect works</h2>
          </div>

          <div className="grid lg:grid-cols-3 gap-8 relative">
            {/* Connector line */}
            <div className="hidden lg:block absolute top-10 left-1/3 right-1/3 h-0.5 bg-gradient-to-r from-brand-200 via-brand-400 to-brand-200" />

            {HOW_IT_WORKS.map((step, i) => (
              <div key={step.title} className="relative">
                <div className="flex flex-col items-center text-center">
                  <div className="w-20 h-20 rounded-2xl bg-brand-50 border-2 border-brand-100 flex items-center justify-center mb-5 relative z-10 shadow-sm">
                    <step.icon className="w-9 h-9 text-brand-600" />
                    <span className="absolute -top-2.5 -right-2.5 w-6 h-6 bg-brand-600 text-white text-xs font-bold rounded-full flex items-center justify-center">{i + 1}</span>
                  </div>
                  <h3 className="text-lg font-bold text-slate-900 mb-2">{step.title}</h3>
                  <p className="text-slate-500 text-sm leading-relaxed">{step.desc}</p>
                </div>
              </div>
            ))}
          </div>

          {/* Daily timeline */}
          <div className="mt-16 bg-slate-50 rounded-2xl border border-slate-200 p-8">
            <h3 className="text-base font-bold text-slate-900 mb-5">A Day in TIQCollect</h3>
            <div className="space-y-3">
              {TIMELINE.map((t) => (
                <div key={t.time} className="flex items-start gap-4">
                  <span className="w-16 text-xs font-bold text-brand-600 flex-shrink-0 mt-0.5">{t.time}</span>
                  <div className="flex-shrink-0 w-2 h-2 rounded-full bg-brand-400 mt-1.5" />
                  <p className="text-sm text-slate-600">{t.event}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ─── Two views side by side ─── */}
      <section className="py-20 lg:py-28 bg-slate-900 text-white overflow-hidden">
        <div className="max-w-7xl mx-auto px-6 lg:px-8">
          <div className="text-center mb-14">
            <span className="text-sm font-semibold text-brand-300 uppercase tracking-wider">Two Interfaces</span>
            <h2 className="text-3xl lg:text-4xl font-bold text-white mt-2">Built for both the field and the office</h2>
          </div>

          <div className="grid lg:grid-cols-2 gap-8">
            {/* Agent card */}
            <div className="bg-white/5 border border-white/10 rounded-2xl p-8 hover:bg-white/8 transition-colors">
              <div className="flex items-center gap-3 mb-5">
                <div className="w-10 h-10 bg-brand-500/20 rounded-xl flex items-center justify-center">
                  <Phone className="w-5 h-5 text-brand-300" />
                </div>
                <div>
                  <h3 className="font-bold text-white">Field Agent App</h3>
                  <p className="text-xs text-white/50">Mobile PWA · Works offline</p>
                </div>
              </div>
              <ul className="space-y-3">
                {AGENT_FEATURES.map((f) => (
                  <li key={f} className="flex items-start gap-2.5 text-sm text-white/70">
                    <CheckCircle className="w-4 h-4 text-brand-400 flex-shrink-0 mt-0.5" />
                    {f}
                  </li>
                ))}
              </ul>
              <button onClick={() => goLogin("agent")} className="mt-6 w-full flex items-center justify-center gap-2 bg-brand-500 hover:bg-brand-600 text-white font-semibold py-3 rounded-xl transition-colors text-sm">
                Try Agent Demo <ArrowRight className="w-4 h-4" />
              </button>
            </div>

            {/* Manager card */}
            <div className="bg-white/5 border border-white/10 rounded-2xl p-8 hover:bg-white/8 transition-colors">
              <div className="flex items-center gap-3 mb-5">
                <div className="w-10 h-10 bg-purple-500/20 rounded-xl flex items-center justify-center">
                  <BarChart3 className="w-5 h-5 text-purple-300" />
                </div>
                <div>
                  <h3 className="font-bold text-white">Agency Manager Dashboard</h3>
                  <p className="text-xs text-white/50">Desktop · Real-time insights</p>
                </div>
              </div>
              <ul className="space-y-3">
                {MANAGER_FEATURES.map((f) => (
                  <li key={f} className="flex items-start gap-2.5 text-sm text-white/70">
                    <CheckCircle className="w-4 h-4 text-purple-400 flex-shrink-0 mt-0.5" />
                    {f}
                  </li>
                ))}
              </ul>
              <button onClick={() => goLogin("manager")} className="mt-6 w-full flex items-center justify-center gap-2 bg-purple-500 hover:bg-purple-600 text-white font-semibold py-3 rounded-xl transition-colors text-sm">
                Try Manager Demo <ArrowRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>
      </section>

      {/* ─── RBI Compliance ─── */}
      <section id="compliance" className="py-20 lg:py-28 bg-white">
        <div className="max-w-7xl mx-auto px-6 lg:px-8">
          <div className="grid lg:grid-cols-2 gap-16 items-center">
            <div>
              <span className="text-sm font-semibold text-success-600 uppercase tracking-wider">RBI Compliance</span>
              <h2 className="text-3xl lg:text-4xl font-bold text-slate-900 mt-2 mb-5">Built-in regulatory compliance</h2>
              <p className="text-slate-500 leading-relaxed mb-8">
                Every feature is designed around the RBI's Guidelines for Recovery Agents (RBA/2008). Enforcement happens at the API layer, not as an afterthought.
              </p>
              <div className="space-y-4">
                {COMPLIANCE.map((c) => (
                  <div key={c.rule} className="flex items-start gap-3">
                    <div className="w-6 h-6 rounded-full bg-success-100 flex items-center justify-center flex-shrink-0 mt-0.5">
                      <CheckCircle className="w-3.5 h-3.5 text-success-600" />
                    </div>
                    <div>
                      <p className="text-sm font-semibold text-slate-900">{c.rule}</p>
                      <p className="text-xs text-slate-500 mt-0.5">{c.desc}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
            <div className="bg-success-50 border border-success-100 rounded-2xl p-8">
              <div className="flex items-center gap-3 mb-6">
                <ShieldCheck className="w-7 h-7 text-success-600" />
                <h3 className="text-lg font-bold text-slate-900">Compliance Scorecard</h3>
              </div>
              <div className="space-y-4">
                {[
                  { label: "Contact Hours Enforcement", score: 100 },
                  { label: "Agent ID Verification", score: 100 },
                  { label: "Geo-stamped Visit Records", score: 100 },
                  { label: "Immutable Audit Logs", score: 100 },
                  { label: "No Sunday Collections", score: 100 },
                  { label: "Customer Consent Recording", score: 94 },
                ].map((r) => (
                  <div key={r.label}>
                    <div className="flex justify-between text-sm mb-1">
                      <span className="text-slate-700 font-medium">{r.label}</span>
                      <span className="font-bold text-success-600">{r.score}%</span>
                    </div>
                    <div className="w-full bg-success-100 rounded-full h-2">
                      <div className="h-2 rounded-full bg-success-500" style={{ width: `${r.score}%` }} />
                    </div>
                  </div>
                ))}
              </div>
              <div className="mt-6 pt-5 border-t border-success-200">
                <div className="flex items-center gap-2">
                  <ShieldCheck className="w-5 h-5 text-success-600" />
                  <span className="text-sm font-bold text-success-700">Overall: 99/100 — Compliant</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ─── Impact ─── */}
      <section id="impact" className="py-20 lg:py-28 bg-slate-50">
        <div className="max-w-7xl mx-auto px-6 lg:px-8">
          <div className="text-center mb-14">
            <span className="text-sm font-semibold text-brand-600 uppercase tracking-wider">Business Impact</span>
            <h2 className="text-3xl lg:text-4xl font-bold text-slate-900 mt-2">Results that matter to your bottom line</h2>
          </div>
          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
            {IMPACT.map((item) => (
              <div key={item.stat} className="bg-white rounded-2xl border border-slate-100 p-7 shadow-sm hover:shadow-md transition-shadow">
                <div className="text-3xl mb-3">{item.icon}</div>
                <p className="text-3xl font-extrabold text-slate-900 mb-1">{item.stat}</p>
                <p className="text-sm font-semibold text-slate-700 mb-2">{item.label}</p>
                <p className="text-xs text-slate-400 leading-relaxed">{item.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ─── CTA ─── */}
      <section className="py-20 bg-gradient-to-br from-brand-600 to-brand-800 text-white">
        <div className="max-w-4xl mx-auto px-6 lg:px-8 text-center">
          <h2 className="text-3xl lg:text-4xl font-bold mb-4">Ready to transform your collections?</h2>
          <p className="text-brand-100 text-lg mb-8 max-w-xl mx-auto">
            See how TIQCollect turns manual, inefficient field collections into a data-driven, RBI-compliant operation.
          </p>
          <div className="flex flex-wrap justify-center gap-4">
            <button onClick={() => goLogin("manager")} className="flex items-center gap-2 bg-white text-brand-700 font-bold px-8 py-3.5 rounded-xl hover:bg-brand-50 transition-colors shadow-lg text-sm">
              <BarChart3 className="w-4 h-4" /> Open Manager Dashboard
            </button>
            <button onClick={() => goLogin("agent")} className="flex items-center gap-2 bg-brand-500/30 border border-brand-300/40 text-white font-semibold px-8 py-3.5 rounded-xl hover:bg-brand-500/40 transition-colors text-sm">
              <Phone className="w-4 h-4" /> Try Agent Mobile App
            </button>
          </div>
          <p className="text-brand-200 text-xs mt-6">No account needed · Demo data loaded · Instant access</p>
        </div>
      </section>

      {/* ─── Footer ─── */}
      <footer className="bg-slate-900 text-white py-12">
        <div className="max-w-7xl mx-auto px-6 lg:px-8">
          <div className="grid md:grid-cols-4 gap-8 mb-8">
            <div className="md:col-span-2">
              <div className="flex items-center gap-2 mb-3">
                <div className="w-7 h-7 bg-brand-600 rounded-lg flex items-center justify-center">
                  <ShieldCheck className="w-4 h-4 text-white" />
                </div>
                <span className="text-lg font-bold">TIQCollect</span>
              </div>
              <p className="text-slate-400 text-sm leading-relaxed max-w-xs">
                Enterprise collections management platform built for Indian NBFCs, banks, and recovery agencies. RBI compliant, AI-powered.
              </p>
            </div>
            <div>
              <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Platform</p>
              <ul className="space-y-2 text-sm text-slate-400">
                {["Manager Dashboard", "Field Agent App", "Beat Map", "ML Allocation", "RBI Compliance"].map((l) => (
                  <li key={l}><a href="#" className="hover:text-white transition-colors">{l}</a></li>
                ))}
              </ul>
            </div>
            <div>
              <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Company</p>
              <ul className="space-y-2 text-sm text-slate-400">
                {["About", "Privacy Policy", "Terms of Service", "RBI Guidelines", "Contact"].map((l) => (
                  <li key={l}><a href="#" className="hover:text-white transition-colors">{l}</a></li>
                ))}
              </ul>
            </div>
          </div>
          <div className="pt-8 border-t border-slate-800 flex flex-col sm:flex-row items-center justify-between gap-4 text-xs text-slate-500">
            <p>© 2025 TIQCollect. All rights reserved. Compliant with RBI/2008 Recovery Agent Guidelines.</p>
            <div className="flex items-center gap-1.5">
              <Globe className="w-3.5 h-3.5" />
              <span>India · EN</span>
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
}

// ─── Static data ─────────────────────────────────────────────────────────────

const FEATURES = [
  {
    icon: Brain,
    color: "bg-purple-50 text-purple-600 border-purple-100",
    title: "ML Case Allocation",
    desc: "Overnight XGBoost-powered allocation weighs DPD, outstanding amount, agent tier, geo-proximity, and historical performance to assign the right cases to the right agents.",
  },
  {
    icon: Map,
    color: "bg-brand-50 text-brand-600 border-brand-100",
    title: "Optimised Beat Maps",
    desc: "Geo-clustered nearest-insertion routing minimises travel time. Agents follow a smart sequence with Google Maps deep-link navigation to each stop.",
  },
  {
    icon: AlertTriangle,
    color: "bg-danger-50 text-danger-600 border-danger-100",
    title: "One-tap SOS",
    desc: "Agents can trigger a geo-stamped emergency alert visible instantly on the manager dashboard. Emergency contacts notified within seconds.",
  },
  {
    icon: ShieldCheck,
    color: "bg-success-50 text-success-600 border-success-100",
    title: "RBI Compliance Engine",
    desc: "Contact hours (8AM–7PM), no Sunday visits, agent ID verification, customer consent recording, and immutable 5-year audit logs — enforced at API level.",
  },
  {
    icon: BarChart3,
    color: "bg-orange-50 text-orange-600 border-orange-100",
    title: "Real-time Manager Dashboard",
    desc: "Live KPIs: collections vs target, agent leaderboard, DPD bucket breakdown, PTP pipeline, SOS alerts, and compliance scorecard.",
  },
  {
    icon: FileCheck,
    color: "bg-teal-50 text-teal-600 border-teal-100",
    title: "Digital Visit Recording",
    desc: "Selfie check-in, GPS verification, premises photos, payment receipts, PTP commitment capture — every visit fully documented with tamper-evident audit trail.",
  },
  {
    icon: IndianRupee,
    color: "bg-emerald-50 text-emerald-600 border-emerald-100",
    title: "Multi-mode Payment Collection",
    desc: "Cash, UPI, NEFT, RTGS, cheque, DD — receipt generation with duplicate detection, agent-to-manager approval workflow, and bank-level reconciliation.",
  },
  {
    icon: Star,
    color: "bg-yellow-50 text-yellow-600 border-yellow-100",
    title: "Agent Ranking Engine",
    desc: "Tier-based ranking (Tier 1/2/3) built from collection rate, PTP conversions, visit efficiency, and time-to-resolve. Drives allocation priority and incentives.",
  },
  {
    icon: Bell,
    color: "bg-pink-50 text-pink-600 border-pink-100",
    title: "Smart PTP Management",
    desc: "Promise-to-Pay tracking with auto-reminders, broken PTP escalation, rescheduling workflow, and conversion rate analytics per agent.",
  },
];

const HOW_IT_WORKS = [
  {
    icon: Brain,
    title: "Overnight ML Allocation",
    desc: "Every night at 8PM, the ML engine scores all open cases and distributes them across agents based on DPD, risk, geo-proximity, and agent rankings. Beat plans are ready by 6AM.",
  },
  {
    icon: Navigation,
    title: "Agents Execute on Beat",
    desc: "Agents check in with a selfie, receive their optimised beat map, navigate case-to-case with Google Maps, and record visits — payments, PTPs, photos — all on mobile.",
  },
  {
    icon: TrendingUp,
    title: "Managers Oversee in Real-time",
    desc: "The manager dashboard shows live collection progress, agent locations, SOS alerts, PTP pipeline, and compliance status. Bank Command Centre accesses via REST API.",
  },
];

const TIMELINE = [
  { time: "8:00 PM", event: "ML allocation engine runs — scores 700+ cases across DPD, risk, geo, and agent factors" },
  { time: "6:00 AM", event: "Beat plans pushed to all agents — geo-optimised routes pre-calculated overnight" },
  { time: "9:00 AM", event: "PTP reminders sent — agents receive alerts for today's promise-to-pay follow-ups" },
  { time: "9:30 AM", event: "Agents check in with selfie + GPS stamp — duty status goes live on manager dashboard" },
  { time: "10AM–6PM", event: "Field visits executed — payments, PTPs, escalations recorded in real-time" },
  { time: "7:00 PM", event: "Contact hours close — visit recording disabled, daily summary generated for managers" },
];

const AGENT_FEATURES = [
  "Selfie attendance check-in with liveness detection",
  "Geo-optimised beat map with Google Maps navigation",
  "Customer detail: loan info, DPD, visit history, active PTP",
  "6-step visit recording: outcome → payment → PTP → photos",
  "One-tap SOS with GPS geo-stamp",
  "Contact hour indicator (RBI 8AM–7PM enforcement)",
  "Monthly performance stats and ranking score",
];

const MANAGER_FEATURES = [
  "Live dashboard: agents on duty, collections vs target",
  "50-agent leaderboard with tier, score, and monthly KPIs",
  "Case table with DPD/priority/status filters",
  "Real-time SOS alert with respond button",
  "Analytics: monthly trend, DPD bucket collection rates",
  "RBI compliance scorecard and audit log browser",
  "Bank Command Centre integration via REST API",
];

const COMPLIANCE = [
  { rule: "Contact Hours: 8AM – 7PM only", desc: "API blocks visit recording outside permitted hours. Frontend shows warning banner." },
  { rule: "No Sunday collections", desc: "Visit recording disabled on Sundays at the API middleware layer." },
  { rule: "Agent identity verification", desc: "ID card number on every visit record. Selfie check-in required before field work." },
  { rule: "Geo-stamped audit trail", desc: "GPS coordinates recorded on check-in, every visit, and payment collection." },
  { rule: "Immutable audit logs (5-year)", desc: "All actions logged with timestamp, user, and IP. No deletes or updates permitted." },
  { rule: "Customer consent & language preference", desc: "Language stored on customer record. Consent flag on document uploads." },
];

const IMPACT = [
  { icon: "📈", stat: "3.2×", label: "Higher collection rate", desc: "ML allocation + optimised routing closes significantly more cases per agent per day versus manual assignment." },
  { icon: "⏱️", stat: "67%", label: "Less time on routing", desc: "Geo-optimised beat maps eliminate agents crossing each other's paths, cutting wasted travel time by two-thirds." },
  { icon: "💰", stat: "₹4.2L", label: "Avg monthly per agent", desc: "Agents using TIQCollect collect on average ₹4.2L per month versus ₹1.3L with traditional paper-based systems." },
  { icon: "✅", stat: "99%", label: "Compliance rate", desc: "Zero RBI violations. Every visit is geo-verified, time-stamped, and logged to an immutable audit trail." },
  { icon: "🤝", stat: "78%", label: "PTP conversion rate", desc: "Smart PTP reminders and follow-up prioritisation drives near 80% promise-to-pay conversion among Tier 1 agents." },
  { icon: "🔔", stat: "<30s", label: "SOS response time", desc: "Manager receives geo-tagged SOS alert with agent location within 30 seconds of agent triggering emergency." },
];

function FeatureCard({ icon: Icon, color, title, desc }: { icon: React.ElementType; color: string; title: string; desc: string }) {
  return (
    <div className="bg-white rounded-2xl border border-slate-100 p-7 shadow-sm hover:shadow-md hover:-translate-y-0.5 transition-all duration-200">
      <div className={`inline-flex w-12 h-12 rounded-xl border items-center justify-center mb-5 ${color}`}>
        <Icon className="w-6 h-6" />
      </div>
      <h3 className="text-base font-bold text-slate-900 mb-2">{title}</h3>
      <p className="text-sm text-slate-500 leading-relaxed">{desc}</p>
    </div>
  );
}
