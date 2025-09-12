"use client";
import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabaseClient";
import QRCode from "qrcode.react";

type Step = "login" | "mfa_challenge" | "mfa_enroll" | "mfa_verify" | "done";

export default function LoginPage() {
  const [step, setStep] = useState<Step>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState<string | null>(null);

  // datos de enrolamiento/challenge
  const [enrollId, setEnrollId] = useState<string>("");
  const [otpUri, setOtpUri] = useState<string>("");
  const [otpCode, setOtpCode] = useState<string>("");
  const [verifiedFactorId, setVerifiedFactorId] = useState<string>(""); // para challenge
  const [challengeId, setChallengeId] = useState<string>("");

  useEffect(() => {
    // si ya hay sesión, manda a /app
    (async () => {
      const { data: { session } } = await supabase.auth.getSession();
      if (session) window.location.href = "/app";
    })();
  }, []);

  const handleLogin = async () => {
    try {
      setError(null); setStatus("Autenticando…");
      const { data, error } = await supabase.auth.signInWithPassword({ email, password });
      if (error) {
        // si mfa requerida, supabase puede marcarlo por mensaje
        if (error.message?.toLowerCase().includes("mfa")) {
          // listar factores para saber si ya hay TOTP verificado
          await routeAfterLogin();
          return;
        }
        setError(error.message); setStatus(""); return;
      }
      await routeAfterLogin();
    } catch (e:any) {
      setError(e?.message || String(e));
      setStatus("");
    }
  };

  const routeAfterLogin = async () => {
    setStatus("Comprobando MFA…");
    const { data: factorsData, error: fErr } = await supabase.auth.mfa.listFactors();
    if (fErr) { setError(fErr.message); setStatus(""); return; }

    const totpVerified = factorsData?.all?.find(f => f.factor_type === "totp" && f.status === "verified");
    if (totpVerified) {
      // ya tiene TOTP → pedir challenge de una
      setVerifiedFactorId(totpVerified.id);
      setStep("mfa_challenge");
      setStatus("");
      return;
    }

    // No tiene TOTP → enrolar
    const { data: enrollData, error: eErr } = await supabase.auth.mfa.enroll({ factorType: "totp" });
    if (eErr) { setError(eErr.message); setStatus(""); return; }
    setEnrollId(enrollData.id);
    setOtpUri(enrollData.totp?.uri || "");
    setStep("mfa_enroll");
    setStatus("");
  };

  const verifyEnrollment = async () => {
    setStatus("Verificando TOTP…");
    const { data, error } = await supabase.auth.mfa.verify({ factorId: enrollId, code: otpCode });
    if (error) { setError(error.message); setStatus(""); return; }
    // después de verificar, redirige a app
    window.location.href = "/app";
  };

  const startChallenge = async () => {
    setStatus("Creando desafío MFA…");
    const { data, error } = await supabase.auth.mfa.challenge({ factorId: verifiedFactorId });
    if (error) { setError(error.message); setStatus(""); return; }
    setChallengeId(data.id);
    setStatus("");
  };

  const verifyChallenge = async () => {
    setStatus("Verificando código…");
    const { data, error } = await supabase.auth.mfa.verify({ factorId: challengeId, code: otpCode });
    if (error) { setError(error.message); setStatus(""); return; }
    window.location.href = "/app";
  };

  // UI
  return (
    <main className="p-6 max-w-md mx-auto space-y-6">
      <h1 className="text-2xl font-bold">Acceso</h1>
      {status && <p className="text-sm text-gray-600">{status}</p>}
      {error && <p className="text-sm text-red-600">{error}</p>}

      {step === "login" && (
        <div className="space-y-3">
          <input
            className="w-full border rounded px-3 py-2"
            placeholder="email"
            value={email}
            onChange={e=>setEmail(e.target.value)}
            type="email"
          />
          <input
            className="w-full border rounded px-3 py-2"
            placeholder="password"
            value={password}
            onChange={e=>setPassword(e.target.value)}
            type="password"
          />
          <button onClick={handleLogin} className="px-3 py-2 bg-black text-white rounded">
            Entrar
          </button>
        </div>
      )}

      {step === "mfa_enroll" && (
        <div className="space-y-3">
          <p className="text-sm">Escanea este QR con Google Authenticator / Authy y luego introduce el código de 6 dígitos.</p>
          {otpUri && (
            <div className="border rounded p-3 inline-block">
              <QRCode value={otpUri} size={180} />
            </div>
          )}
          <input
            className="w-full border rounded px-3 py-2"
            placeholder="Código 6 dígitos"
            value={otpCode}
            onChange={e=>setOtpCode(e.target.value)}
            inputMode="numeric"
          />
          <button onClick={verifyEnrollment} className="px-3 py-2 bg-black text-white rounded">
            Verificar y entrar
          </button>
        </div>
      )}

      {step === "mfa_challenge" && (
        <div className="space-y-3">
          <p className="text-sm">Introduce tu código TOTP.</p>
          {!challengeId && (
            <button onClick={startChallenge} className="px-3 py-2 bg-gray-200 rounded">
              Generar desafío
            </button>
          )}
          {challengeId && (
            <>
              <input
                className="w-full border rounded px-3 py-2"
                placeholder="Código 6 dígitos"
                value={otpCode}
                onChange={e=>setOtpCode(e.target.value)}
                inputMode="numeric"
              />
              <button onClick={verifyChallenge} className="px-3 py-2 bg-black text-white rounded">
                Verificar y entrar
              </button>
            </>
          )}
        </div>
      )}
    </main>
  );
}
