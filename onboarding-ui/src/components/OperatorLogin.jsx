import React, { useState } from "react";
import { KeyRound, Loader2, ShieldCheck } from "lucide-react";

export default function OperatorLogin({ request, onAuthenticated }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [challenge, setChallenge] = useState("");
  const [setup, setSetup] = useState(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const beginLogin = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await request("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email: email.trim(), password }),
      });
      setPassword("");
      setChallenge(result.mfa_token);
      if (result.mfa_setup_required) {
        const enrollment = await request("/api/auth/mfa/setup", {
          method: "POST",
          body: JSON.stringify({ mfa_token: result.mfa_token }),
        });
        setSetup(enrollment);
      } else if (!result.mfa_required) {
        throw new Error("The server returned an unexpected operator login response.");
      }
    } catch (requestError) {
      setError(requestError.message || "Operator sign-in failed.");
    } finally {
      setBusy(false);
    }
  };

  const verifyCode = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await request(
        setup ? "/api/auth/mfa/confirm" : "/api/auth/mfa/verify-login",
        {
          method: "POST",
          body: JSON.stringify({ mfa_token: challenge, code }),
        },
      );
      if (!result.access_token) {
        throw new Error("The server did not return an authenticated operator session.");
      }
      onAuthenticated({ token: result.access_token, email: email.trim() });
    } catch (requestError) {
      setError(requestError.message || "MFA verification failed.");
    } finally {
      setBusy(false);
    }
  };

  const resetChallenge = () => {
    setChallenge("");
    setSetup(null);
    setCode("");
    setError("");
  };

  return (
    <main className="min-h-screen theme-panel flex items-center justify-center p-4">
      <section className="w-full max-w-md theme-elevated rounded-2xl border border-slate-500/20 p-6 sm:p-8 space-y-5 shadow-xl">
        <div className="flex items-center gap-3">
          <div className="w-11 h-11 rounded-xl theme-accent-bg flex items-center justify-center">
            <ShieldCheck className="w-5 h-5" />
          </div>
          <div>
            <h1 className="text-lg font-black">AVINYA NEXUS</h1>
            <p className="text-xs theme-text-muted">Named control-plane operator access</p>
          </div>
        </div>

        {!challenge ? (
          <form onSubmit={beginLogin} className="space-y-4">
            <p className="text-sm theme-text-muted">
              Sign in with your operator account. MFA is required for every session.
            </p>
            <label className="block space-y-1.5">
              <span className="text-xs font-bold">Operator email</span>
              <input
                type="email"
                autoComplete="username"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                className="w-full theme-input rounded-xl px-3 py-2.5 text-sm"
              />
            </label>
            <label className="block space-y-1.5">
              <span className="text-xs font-bold">Password</span>
              <input
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="w-full theme-input rounded-xl px-3 py-2.5 text-sm"
              />
            </label>
            {error && <p role="alert" className="text-sm text-rose-500">{error}</p>}
            <button
              type="submit"
              disabled={busy}
              className="w-full theme-accent-bg rounded-xl py-2.5 text-sm font-bold flex items-center justify-center gap-2 disabled:opacity-60"
            >
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <KeyRound className="w-4 h-4" />}
              Continue securely
            </button>
          </form>
        ) : (
          <form onSubmit={verifyCode} className="space-y-4">
            {setup ? (
              <div className="space-y-3 text-sm">
                <p>Set up an authenticator app, then enter its current six-digit code.</p>
                <div className="rounded-xl theme-panel p-3 space-y-2">
                  <p className="text-xs font-bold">Authenticator secret</p>
                  <code className="block break-all select-all text-xs theme-accent-text">
                    {setup.secret}
                  </code>
                  <a
                    href={setup.otpauth_uri}
                    className="text-xs underline theme-accent-text break-all"
                  >
                    Open authenticator app
                  </a>
                </div>
                <p className="text-xs theme-text-muted">
                  Save the secret only in your approved authenticator. It is displayed once during enrollment.
                </p>
              </div>
            ) : (
              <p className="text-sm theme-text-muted">
                Enter the current six-digit code from your authenticator.
              </p>
            )}
            <label className="block space-y-1.5">
              <span className="text-xs font-bold">Authenticator code</span>
              <input
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9]{6}"
                maxLength={6}
                required
                value={code}
                onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
                className="w-full theme-input rounded-xl px-3 py-2.5 text-center text-lg tracking-[0.4em] font-mono"
              />
            </label>
            {error && <p role="alert" className="text-sm text-rose-500">{error}</p>}
            <button
              type="submit"
              disabled={busy || code.length !== 6}
              className="w-full theme-accent-bg rounded-xl py-2.5 text-sm font-bold flex items-center justify-center gap-2 disabled:opacity-60"
            >
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <ShieldCheck className="w-4 h-4" />}
              Verify and sign in
            </button>
            <button
              type="button"
              onClick={resetChallenge}
              disabled={busy}
              className="w-full rounded-xl py-2 text-xs theme-text-muted hover:theme-text-main"
            >
              Start over
            </button>
          </form>
        )}
      </section>
    </main>
  );
}
