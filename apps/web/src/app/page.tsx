"use client";
import { useRef, useState, useEffect } from "react";
import { supabase } from "@/lib/supabaseClient";

const MAX_MS = 15000; // 15s

type Profile = {
  user_id: string;
  native_lang: string;
  target_lang: string;
  full_name?: string | null;
};

export default function Home() {
  const [rec, setRec] = useState<MediaRecorder | null>(null);
  const chunks = useRef<BlobPart[]>([]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [text, setText] = useState("");
  const [reply, setReply] = useState("");
  const [audioURL, setAudioURL] = useState<string>("");
  const [timerLeft, setTimerLeft] = useState<number>(MAX_MS / 1000);
  const timerRef = useRef<number | null>(null);
  const stopTimeoutRef = useRef<number | null>(null);

  const [profile, setProfile] = useState<Profile | null>(null);
  const [userEmail, setUserEmail] = useState<string | null>(null);

  // Precalienta API + asegura perfil
  useEffect(() => {
    (async () => {
      const base = process.env.NEXT_PUBLIC_API_BASE!;
      fetch(`${base}/health`).catch(() => {});
      // sesión
      const { data: sessionData } = await supabase.auth.getSession();
      const session = sessionData.session;
      if (!session) { window.location.href = "/login"; return; }
      setUserEmail(session.user.email ?? null);
      // perfil
      const { data: p } = await supabase.from("profiles").select("*").eq("user_id", session.user.id).maybeSingle();
      if (!p) { window.location.href = "/profile"; return; }
      setProfile(p as Profile);
    })();
  }, []);

  const pickMime = () => {
    if (typeof MediaRecorder === "undefined") return undefined;
    if (MediaRecorder.isTypeSupported("audio/webm;codecs=opus")) return "audio/webm;codecs=opus";
    if (MediaRecorder.isTypeSupported("audio/webm")) return "audio/webm";
    return undefined;
  };

  const startCountdown = () => {
    setTimerLeft(Math.round(MAX_MS / 1000));
    clearCountdown();
    timerRef.current = window.setInterval(() => {
      setTimerLeft((s) => (s > 0 ? s - 1 : 0));
    }, 1000);
  };

  const clearCountdown = () => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  };

  const startRec = async () => {
    if (busy) return;
    if (!profile) { setStatus("Perfil no cargado"); return; }

    setBusy(true);
    setStatus("Solicitando micrófono…");
    setText("");
    setReply("");
    setAudioURL("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = pickMime();
      const mr = mimeType
        ? new MediaRecorder(stream, { mimeType, audioBitsPerSecond: 64000 })
        : new MediaRecorder(stream);

      chunks.current = [];
      mr.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunks.current.push(e.data);
      };

      mr.onstop = async () => {
        clearCountdown();
        if (stopTimeoutRef.current) {
          clearTimeout(stopTimeoutRef.current);
          stopTimeoutRef.current = null;
        }
        try {
          const mime = mr.mimeType || "audio/webm";
          const blob = new Blob(chunks.current, { type: mime });
          chunks.current = [];
          if (!blob.size) {
            setStatus("No se capturó audio (habla 2–3s antes de detener).");
            setBusy(false);
            return;
          }

          const base = process.env.NEXT_PUBLIC_API_BASE!;
          const t0 = performance.now();

          // --- ASR ---
          setStatus("Subiendo audio…");
          const fd = new FormData();
          fd.append("audio", blob, mime.includes("webm") ? "input.webm" : "input.wav");
          // p.ej. si practican inglés:
          fd.append("language", profile.target_lang || "en");

          setStatus("Transcribiendo…");
          const asrResp = await fetch(`${base}/asr`, { method: "POST", body: fd });
          const tAsrEnd = performance.now();
          const asrJson = await asrResp.json();
          if (!asrResp.ok) {
            setStatus(`ASR ${asrResp.status}: ${asrJson?.error || "error"}`);
            setBusy(false);
            return;
          }
          setText(asrJson.text || "");

          // --- CHAT (usar native/target del perfil) ---
          setStatus("Corrigiendo…");
          const fd2 = new FormData();
          fd2.append("prompt", asrJson.text || "");
          fd2.append("native_language", profile.native_lang || "es");
          fd2.append("target_language", profile.target_lang || "en");
          const chatResp = await fetch(`${base}/chat`, { method: "POST", body: fd2 });
          const tChatEnd = performance.now();
          const chatJson = await chatResp.json();
          if (!chatResp.ok) {
            setStatus(`CHAT ${chatResp.status}: ${chatJson?.error || "error"}`);
            setBusy(false);
            return;
          }
          setReply(chatJson.text || "");

          // --- TTS ---
          setStatus("Generando voz…");
          const fd3 = new FormData();
          fd3.append("text", chatJson.text || "");
          fd3.append("voice", "alloy"); // opcional: podría variar según native_lang
          fd3.append("format", "mp3");
          const ttsResp = await fetch(`${base}/tts`, { method: "POST", body: fd3 });
          const tTtsEnd = performance.now();
          if (!ttsResp.ok) {
            const j = await ttsResp.json().catch(() => null);
            setStatus(`TTS ${ttsResp.status}: ${j?.error || "error"}`);
            setBusy(false);
            return;
          }
          const ab = await ttsResp.arrayBuffer();
          const url = URL.createObjectURL(new Blob([ab], { type: "audio/mpeg" }));
          setAudioURL(url);

          const asrMs = Math.round(tAsrEnd - t0);
          const chatMs = Math.round(tChatEnd - tAsrEnd);
          const ttsMs = Math.round(tTtsEnd - tChatEnd);
          setStatus(`Listo ✅ (ASR ${asrMs}ms, Chat ${chatMs}ms, TTS ${ttsMs}ms)`);
        } catch (err: unknown) {
          const msg = err instanceof Error ? err.message : String(err);
          setStatus(`Error: ${msg}`);
        } finally {
          setBusy(false);
        }
      };

      mr.start(250);
      setRec(mr);
      startCountdown();
      setStatus(`Grabando… (se corta en ${Math.round(MAX_MS / 1000)}s)`);

      stopTimeoutRef.current = window.setTimeout(() => {
        if (mr.state === "recording") mr.stop();
      }, MAX_MS);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setStatus(`No se pudo iniciar grabación: ${msg}`);
      setBusy(false);
    }
  };

  const stopRec = () => {
    if (!rec) return;
    if (rec.state === "recording") {
      rec.stop();
      setStatus("Procesando…");
    }
  };

  if (!profile) {
    return (
      <main className="p-6">
        Cargando perfil…
      </main>
    );
  }

  return (
    <main className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">English Coach (MVP web)</h1>
        <div className="flex items-center gap-2">
          <span className="text-sm text-gray-600">
            {userEmail} | {profile.native_lang}→{profile.target_lang}
          </span>
          <button
            onClick={() => (window.location.href = "/profile")}
            className="px-3 py-2 bg-gray-200 rounded"
          >
            Perfil
          </button>
          <button
            onClick={async () => { await supabase.auth.signOut(); window.location.href = "/login"; }}
            className="px-3 py-2 bg-gray-200 rounded"
          >
            Cerrar sesión
          </button>
        </div>
      </div>

      <div className="text-sm text-gray-700">
        <b>Nota:</b> cada turno graba hasta <b>15 segundos</b>. Tiempo restante:{" "}
        <span className="font-mono">{timerLeft}s</span>
      </div>

      <div className="space-x-2">
        <button
          onClick={startRec}
          disabled={busy}
          className={`px-3 py-2 rounded text-white ${busy ? "bg-gray-400" : "bg-black"}`}
        >
          Grabar
        </button>
        <button
          onClick={stopRec}
          disabled={!rec || busy}
          className="px-3 py-2 bg-gray-200 rounded"
        >
          Detener
        </button>
      </div>

      <p className="text-sm text-gray-600">{status}</p>

      <div className="space-y-2">
        <p><b>Tú dijiste:</b> {text}</p>
        <p><b>Coach:</b> {reply}</p>
      </div>

      {audioURL && <audio src={audioURL} controls autoPlay />}

      {busy && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center">
          <div className="bg-white rounded-lg px-4 py-3 shadow">
            <div className="animate-pulse">Procesando…</div>
            <div className="text-xs text-gray-600 mt-1">{status}</div>
          </div>
        </div>
      )}
    </main>
  );
}
