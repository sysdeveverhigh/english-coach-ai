"use client";
import { useEffect, useState } from "react";
import { SUPPORTED_VOICES, Voice, getSavedVoice, saveVoice } from "@/lib/voices";

export default function SettingsPage() {
  const [voice, setVoice] = useState<Voice | "">("");

  useEffect(() => {
    const v = getSavedVoice();
    setVoice(v || "");
  }, []);

  return (
    <main className="max-w-xl mx-auto p-6 space-y-6">
      <h1 className="text-2xl font-semibold">Configuración</h1>
      <section className="space-y-3">
        <label className="block text-sm font-medium">Voz de la profesora</label>
        <select
          className="border rounded px-3 py-2 w-full"
          value={voice}
          onChange={(e) => setVoice(e.target.value as Voice)}
        >
          <option value="" disabled>Selecciona una voz…</option>
          {SUPPORTED_VOICES.map(v => (
            <option key={v} value={v}>{v}</option>
          ))}
        </select>
        <div className="flex gap-2">
          <button
            className="bg-black text-white rounded px-4 py-2"
            onClick={() => {
              if (!voice) return alert("Elige una voz");
              saveVoice(voice as Voice);
              alert(`Voz guardada: ${voice}`);
            }}
          >
            Guardar
          </button>
        </div>
        <p className="text-xs text-gray-500">
          Esta preferencia se guarda en tu navegador y se usará en todas las clases.
        </p>
      </section>
    </main>
  );
}
