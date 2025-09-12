"use client";
import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabaseClient";

const LANGS = [
  { code: "en", label: "English" },
  { code: "es", label: "Español" },
  // agrega más luego
];

const AGES = ["<18","18-24","25-34","35-44","45-54","55+"];

export default function ProfilePage() {
  const [loading, setLoading] = useState(true);
  const [fullName, setFullName] = useState("");
  const [ageRange, setAgeRange] = useState<string>("25-34");
  const [nativeLang, setNativeLang] = useState<string>("es");
  const [targetLang, setTargetLang] = useState<string>("en");
  const [error, setError] = useState<string | null>(null);
  const [userId, setUserId] = useState<string>("");

  useEffect(() => {
    (async () => {
      const { data: sessionData } = await supabase.auth.getSession();
      const session = sessionData.session;
      if (!session) {
        window.location.href = "/login";
        return;
      }
      setUserId(session.user.id);

      const { data: profile } = await supabase
        .from("profiles")
        .select("*")
        .eq("user_id", session.user.id)
        .maybeSingle();

      if (profile) {
        setFullName(profile.full_name ?? "");
        setAgeRange(profile.age_range ?? "25-34");
        setNativeLang(profile.native_lang ?? "es");
        setTargetLang(profile.target_lang ?? "en");
      }
      setLoading(false);
    })();
  }, []);

  const save = async () => {
    setError(null);
    if (!userId) return;

    const payload = {
      user_id: userId,
      full_name: fullName || null,
      age_range: ageRange,
      native_lang: nativeLang,
      target_lang: targetLang,
    };

    // upsert por PK user_id
    const { error } = await supabase.from("profiles").upsert(payload, { onConflict: "user_id" });
    if (error) {
      setError(error.message);
      return;
    }
    window.location.href = "/";
  };

  if (loading) return <main className="p-6">Cargando…</main>;

  return (
    <main className="p-6 max-w-md mx-auto space-y-5">
      <h1 className="text-2xl font-bold">Tu perfil</h1>
      <p className="text-sm text-gray-600">
        Usa tu idioma nativo para explicaciones y el idioma meta para practicar.
      </p>

      {error && <p className="text-sm text-red-600">{error}</p>}

      <div className="space-y-3">
        <label className="block text-sm">Nombre (opcional)</label>
        <input className="w-full border rounded px-3 py-2"
               value={fullName}
               onChange={e=>setFullName(e.target.value)}
               placeholder="Tu nombre" />

        <label className="block text-sm mt-3">Rango de edad</label>
        <select className="w-full border rounded px-3 py-2"
                value={ageRange}
                onChange={e=>setAgeRange(e.target.value)}>
          {AGES.map(a => <option key={a} value={a}>{a}</option>)}
        </select>

        <label className="block text-sm mt-3">Idioma nativo</label>
        <select className="w-full border rounded px-3 py-2"
                value={nativeLang}
                onChange={e=>setNativeLang(e.target.value)}>
          {LANGS.map(l => <option key={l.code} value={l.code}>{l.label}</option>)}
        </select>

        <label className="block text-sm mt-3">Idioma a aprender</label>
        <select className="w-full border rounded px-3 py-2"
                value={targetLang}
                onChange={e=>setTargetLang(e.target.value)}>
          {LANGS.map(l => <option key={l.code} value={l.code}>{l.label}</option>)}
        </select>

        <button onClick={save} className="mt-4 px-3 py-2 bg-black text-white rounded">
          Guardar
        </button>
      </div>
    </main>
  );
}
