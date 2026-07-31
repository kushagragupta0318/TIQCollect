import { useState, type FormEvent, useEffect } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { Eye, EyeOff, Lock, Mail, ShieldCheck } from "lucide-react";
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
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-brand-900 via-brand-700 to-brand-500 p-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 bg-white rounded-2xl shadow-lg mb-4">
            <ShieldCheck className="w-9 h-9 text-brand-600" />
          </div>
          <h1 className="text-2xl font-bold text-white">TIQCollect</h1>
          <p className="text-brand-100 text-sm mt-1">Enterprise Collections Platform</p>
        </div>

        <div className="bg-white rounded-2xl shadow-2xl p-8">
          <h2 className="text-lg font-semibold text-slate-900 mb-6">Sign in to your account</h2>
          <form onSubmit={handleSubmit} className="space-y-4">
            <Input label="Email address" type="email" placeholder="you@tiqcollect.in" value={email} onChange={(e) => setEmail(e.target.value)} leftIcon={<Mail className="w-4 h-4" />} autoComplete="email" required />
            <div className="w-full">
              <label className="block text-sm font-medium text-slate-700 mb-1">Password</label>
              <div className="relative">
                <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"><Lock className="w-4 h-4" /></span>
                <input type={showPassword ? "text" : "password"} className="input pl-9 pr-10" placeholder="••••••••" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
                <button type="button" className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600" onClick={() => setShowPassword((v) => !v)} tabIndex={-1}>
                  {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
            </div>
            <Button type="submit" fullWidth loading={loading} size="lg" className="mt-2">Sign In</Button>
          </form>

          {/* Quick login buttons */}
          <div className="mt-5 pt-5 border-t border-slate-100">
            <p className="text-xs text-slate-400 text-center mb-3">Quick demo login</p>
            <div className="grid grid-cols-2 gap-2">
              <button onClick={() => quickLogin("agent")} className="text-xs bg-blue-50 text-blue-700 border border-blue-200 rounded-lg py-2 px-3 font-medium hover:bg-blue-100 transition-colors">
                Field Agent
              </button>
              <button onClick={() => quickLogin("manager")} className="text-xs bg-purple-50 text-purple-700 border border-purple-200 rounded-lg py-2 px-3 font-medium hover:bg-purple-100 transition-colors">
                Manager
              </button>
            </div>
          </div>
        </div>

        <p className="text-center text-brand-200 text-xs mt-6">© 2025 TIQCollect · RBI Compliant</p>
      </div>
    </div>
  );
}
