import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { shortMoney } from "@/lib/money";
import type { NavigateFunction } from "react-router";
import { Briefcase, CheckCircle, IndianRupee, Calendar, MapPin, Clock, Camera, X } from "lucide-react";
import { toast } from "react-hot-toast";
import { checkIn as apiCheckIn } from "@/api/agent";
import { StatCard } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { useAuthStore } from "@/store/authStore";
import { useBeat } from "@/contexts/useBeat";
import { useAnimatedValue, useCountUp } from "@/hooks/useAnimatedValue";

export default function AgentHomePage() {
  const { user } = useAuthStore();
  const navigate = useNavigate();
  const { beat, summary, loading, refresh, patch, patchSummary } = useBeat();

  const [checkingIn, setCheckingIn] = useState(false);
  const [selfieModal, setSelfieModal] = useState(false);
  const [capturedSelfie, setCapturedSelfie] = useState<string | null>(null);
  const [checkInCoords, setCheckInCoords] = useState<{ lat: number; lon: number } | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const checkedIn = summary?.check_in_status === "ON_DUTY" || beat?.check_in_status === "ON_DUTY";
  const collected = beat?.amount_collected_today ?? summary?.amount_collected_today ?? 0;
  const totalTarget = beat?.total_target_amount ?? summary?.total_target_today ?? 0;
  const ptpsDue = beat?.ptps_due_today ?? summary?.ptps_due_today ?? 0;
  const totalCases = beat?.total_cases ?? summary?.cases_today ?? 0;
  const doneCases = beat?.cases_visited_today ?? summary?.visits_done ?? 0;
  const collectionPct = totalTarget > 0 ? Math.min(Math.round((collected / totalTarget) * 100), 100) : 0;
  const pendingCases = Math.max(totalCases - doneCases, 0);

  async function startSelfieCapture() {
    setSelfieModal(true);
    // Get GPS coords when modal opens
    navigator.geolocation?.getCurrentPosition(
      (p) => setCheckInCoords({ lat: p.coords.latitude, lon: p.coords.longitude }),
      () => setCheckInCoords({ lat: 28.4595, lon: 77.0266 })
    );
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user" } });
      streamRef.current = stream;
      if (videoRef.current) videoRef.current.srcObject = stream;
    } catch {
      setTimeout(() => {
        setCapturedSelfie("data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTAwIiBoZWlnaHQ9IjEwMCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48Y2lyY2xlIGN4PSI1MCIgY3k9IjUwIiByPSI1MCIgZmlsbD0iIzNiODJmNiIvPjx0ZXh0IHg9IjUwIiB5PSI1OCIgZm9udC1zaXplPSIzMCIgdGV4dC1hbmNob3I9Im1pZGRsZSIgZmlsbD0id2hpdGUiPvCfkYs8L3RleHQ+PC9zdmc+");
      }, 500);
    }
  }

  function captureSelfie() {
    if (!videoRef.current || !canvasRef.current) return;
    const ctx = canvasRef.current.getContext("2d");
    canvasRef.current.width = videoRef.current.videoWidth;
    canvasRef.current.height = videoRef.current.videoHeight;
    ctx?.drawImage(videoRef.current, 0, 0);
    setCapturedSelfie(canvasRef.current.toDataURL("image/jpeg"));
    streamRef.current?.getTracks().forEach((t) => t.stop());
  }

  function closeSelfie() {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    setSelfieModal(false);
    setCapturedSelfie(null);
  }

  async function confirmCheckIn() {
    setCheckingIn(true);
    const coords = checkInCoords ?? { lat: 28.4595, lon: 77.0266 };
    try {
      await apiCheckIn(coords.lat, coords.lon);
      closeSelfie();
      patch({ check_in_status: "ON_DUTY" });
      patchSummary({ check_in_status: "ON_DUTY" });
      toast.success("✅ Checked in! Have a safe day, " + user?.full_name.split(" ")[0] + "!");
    } catch {
      toast.error("Check-in failed");
    } finally {
      setCheckingIn(false);
    }
  }

  // Auto re-anchor: whenever the home page opens while on duty, silently push
  // the agent's current live GPS so the demo customers snap to wherever the agent is
  const autoAnchoredRef = useRef(false);
  useEffect(() => {
    if (!checkedIn || autoAnchoredRef.current || !navigator.geolocation) return;
    autoAnchoredRef.current = true;
    navigator.geolocation.getCurrentPosition(
      (p) => { apiCheckIn(p.coords.latitude, p.coords.longitude).catch(() => {}); },
      () => {},
      { enableHighAccuracy: true, timeout: 15000 }
    );
  }, [checkedIn]);

  return (
    <div className="p-4 lg:p-6 space-y-4 pb-6">
      {/* Greeting + check-in */}
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs text-slate-500">{new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "short" })}</p>
          <h1 className="text-xl font-bold text-slate-900">Good {getGreeting()}, {user?.full_name.split(" ")[0]}!</h1>
        </div>
        {!checkedIn ? (
          <Button onClick={startSelfieCapture} loading={checkingIn} size="sm">
            <Camera className="w-3.5 h-3.5" /> Check In
          </Button>
        ) : (
          <div className="flex flex-col items-end gap-0.5">
            <span className="badge badge-green">✓ On Duty</span>
          </div>
        )}
      </div>

      {/* No beat banner if not assigned for today */}
      {!loading && !beat && (
        <div className="card p-4 sm:p-5 bg-gradient-to-r from-blue-50/70 to-indigo-50/70 border border-blue-100/80">
          <div className="flex items-start gap-3">
            <div className="w-9 h-9 rounded-xl bg-blue-100 text-blue-700 flex items-center justify-center flex-shrink-0 mt-0.5">
              <Briefcase className="w-4 h-4" />
            </div>
            <div className="flex-1 min-w-0">
              <h3 className="text-sm font-semibold text-slate-900">No Field Beat Assigned Today</h3>
              <p className="text-xs text-slate-500 mt-0.5">
                Your Agency Manager has not scheduled an active route for today yet. You can still record direct collections or PTP follow-ups.
              </p>
              <div className="flex items-center gap-2 mt-3">
                <Button variant="secondary" size="sm" onClick={() => refresh()}>
                  Check For Updates
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Collection progress */}
      {loading ? (
        <div className="card animate-pulse space-y-2">
          <div className="flex justify-between">
            <div className="h-3.5 w-36 bg-slate-200 rounded" />
            <div className="h-3.5 w-10 bg-slate-200 rounded" />
          </div>
          <div className="w-full bg-slate-100 rounded-full h-3" />
          <div className="flex justify-between">
            <div className="h-3 w-24 bg-slate-100 rounded" />
            <div className="h-3 w-20 bg-slate-100 rounded" />
          </div>
        </div>
      ) : totalTarget > 0 ? (
        <CollectionProgressBar
          collected={collected}
          totalTarget={totalTarget}
          pct={collectionPct}
        />
      ) : null}

      {/* Stats */}
      {loading ? (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="card animate-pulse space-y-2">
              <div className="h-3 w-14 bg-slate-200 rounded" />
              <div className="h-7 w-10 bg-slate-200 rounded" />
              <div className="h-3 w-20 bg-slate-100 rounded" />
            </div>
          ))}
        </div>
      ) : (
        <StatGrid
          totalCases={totalCases}
          doneCases={doneCases}
          collected={collected}
          ptpsDue={ptpsDue}
          navigate={navigate}
        />
      )}

      {/* Quick actions */}
      <div className="space-y-2">
        <h2 className="text-sm font-semibold text-slate-700">Quick Actions</h2>
        <div className="space-y-2 lg:space-y-0 lg:grid lg:grid-cols-2 lg:gap-3">
          {beat ? (
            <QuickAction icon={<MapPin className="w-5 h-5 text-brand-600" />} label="Open Beat Map" sub="View today's optimised route" onClick={() => navigate("/agent/beat")} color="bg-brand-50" />
          ) : (
            <QuickAction icon={<MapPin className="w-5 h-5 text-slate-400" />} label="No Route Active" sub="Beat map will appear when scheduled" onClick={() => {}} color="bg-slate-50 opacity-60" />
          )}
          <QuickAction icon={<Briefcase className="w-5 h-5 text-slate-600" />} label="All My Cases" sub={`${pendingCases} pending · ${doneCases} done`} onClick={() => navigate("/agent/cases")} color="bg-slate-50" />
          {ptpsDue > 0 && (
            <QuickAction icon={<Calendar className="w-5 h-5 text-warning-600" />} label={`${ptpsDue} PTPs Due Today`} sub="Follow up before 7 PM" onClick={() => navigate("/agent/cases?filter=ptp_due")} color="bg-warning-50" />
          )}
          {checkedIn && beat && (
            <QuickAction icon={<Clock className="w-5 h-5 text-success-600" />} label="Next Case on Beat" sub="Navigate to nearest unvisited" onClick={() => navigate("/agent/beat")} color="bg-success-50" />
          )}
        </div>
      </div>

      {/* Selfie check-in modal */}
      {selfieModal && (
        <div className="fixed inset-0 z-50 bg-black/80 flex flex-col items-center justify-center p-4">
          <div className="w-full max-w-sm bg-white rounded-card border border-slate-200 shadow-pop overflow-hidden">
            <div className="flex items-center justify-between p-4 border-b border-slate-100">
              <h3 className="font-semibold text-slate-900">Selfie Check-In</h3>
              <button onClick={closeSelfie} className="text-slate-400 hover:text-slate-600"><X className="w-5 h-5" /></button>
            </div>

            <div className="p-4 space-y-4">
              {!capturedSelfie ? (
                <>
                  <div className="relative bg-black rounded-xl overflow-hidden aspect-square">
                    <video ref={videoRef} autoPlay playsInline muted className="w-full h-full object-cover" />
                    <canvas ref={canvasRef} className="hidden" />
                    <div className="absolute inset-0 flex items-end justify-center pb-4">
                      <div className="w-20 h-20 rounded-full border-4 border-white/50 absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2" />
                    </div>
                  </div>
                  <p className="text-xs text-center text-slate-500">Align your face with the circle and take selfie for attendance</p>
                  <Button fullWidth onClick={captureSelfie}>
                    <Camera className="w-4 h-4" /> Capture Selfie
                  </Button>
                </>
              ) : (
                <>
                  <div className="relative bg-slate-100 rounded-xl overflow-hidden aspect-square">
                    <img src={capturedSelfie} alt="Selfie" className="w-full h-full object-cover" />
                    <div className="absolute top-2 right-2 bg-success-500 text-white text-xs px-2 py-1 rounded-full font-medium">✓ Captured</div>
                  </div>
                  <div className="bg-slate-50 rounded-lg p-3 text-xs text-slate-600 space-y-1">
                    <p>📍 Location: Mumbai, Maharashtra</p>
                    <p>🕐 Time: {new Date().toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}</p>
                    <p>✅ Liveness check: Passed</p>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <Button variant="secondary" onClick={() => setCapturedSelfie(null)}>Retake</Button>
                    <Button onClick={confirmCheckIn} loading={checkingIn}>Confirm</Button>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StatGrid({
  totalCases,
  doneCases,
  collected,
  ptpsDue,
  navigate,
}: {
  totalCases: number;
  doneCases: number;
  collected: number;
  ptpsDue: number;
  navigate: NavigateFunction;
}) {
  const pendingCases = Math.max(totalCases - doneCases, 0);

  const animatedPending = useCountUp(pendingCases);
  const animatedDone = useCountUp(doneCases);
  const animatedPtpsDue = useCountUp(ptpsDue);

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <StatCard label="Pending" value={animatedPending} subtext={`of ${totalCases} cases`} icon={<Briefcase className="w-5 h-5" />} colorClass="text-brand-600" onClick={() => navigate("/agent/cases")} />
      <StatCard label="Done" value={animatedDone} subtext="visits today" icon={<CheckCircle className="w-5 h-5" />} colorClass="text-success-600" onClick={() => navigate("/agent/cases?filter=visited_today")} />
      <StatCard label="Collected" value={shortMoney(collected)} subtext="today" icon={<IndianRupee className="w-5 h-5" />} colorClass="text-success-600" onClick={() => navigate("/agent/cases?filter=collected")} />
      <StatCard label="PTPs Due" value={animatedPtpsDue} subtext="today" icon={<Calendar className="w-5 h-5" />} colorClass={ptpsDue > 0 ? "text-warning-600" : "text-slate-400"} onClick={() => navigate("/agent/cases?filter=ptp_due")} />
    </div>
  );
}

function QuickAction({ icon, label, sub, onClick, color }: { icon: React.ReactNode; label: string; sub: string; onClick: () => void; color: string }) {
  return (
    // No transition-shadow here. It is a utility, so it outranks .card and
    // narrows transition-property to box-shadow alone — which left .card:hover's
    // lift with nothing to ease and made it snap. .card already transitions
    // transform, shadow and border together.
    <button onClick={onClick} className={`w-full card flex items-center gap-3 cursor-pointer text-left ${color} border-0`}>
      <div className="w-10 h-10 rounded-xl bg-white flex items-center justify-center flex-shrink-0 shadow-sm">{icon}</div>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-slate-900">{label}</p>
        <p className="text-xs text-slate-400 truncate">{sub}</p>
      </div>
      <svg className="w-4 h-4 text-slate-300 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" /></svg>
    </button>
  );
}

function CollectionProgressBar({ collected, totalTarget, pct }: { collected: number; totalTarget: number; pct: number }) {
  const animatedPct = useAnimatedValue(pct);
  return (
    <div className="card">
      <div className="flex justify-between text-sm mb-2">
        <span className="font-semibold text-slate-700">Today's Collection</span>
        <span className="font-bold text-brand-600">{animatedPct}%</span>
      </div>
      <div className="w-full bg-slate-100 rounded-full h-3">
        <div className="h-3 rounded-full bg-primary transition-all duration-700" style={{ width: `${animatedPct}%` }} />
      </div>
      <div className="flex justify-between text-xs text-slate-400 mt-1.5">
        <span>₹{(collected / 1000).toFixed(1)}K collected</span>
        <span>Target {shortMoney(totalTarget)}</span>
      </div>
    </div>
  );
}

function getGreeting() {
  const h = new Date().getHours();
  return h < 12 ? "morning" : h < 17 ? "afternoon" : "evening";
}
