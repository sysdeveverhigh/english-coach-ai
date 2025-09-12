"use client";
import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabaseClient";
import Link from "next/link";

export default function AppHome() {
  const [ready, setReady] = useState(false);
  const [userEmail, setUserEmail] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) {
        window.location.href = "/login";
        return;
      }
      setUserEmail(session.user.email ?? null);
      setReady(true);
    })();
  }, []);

  if (!ready) return <main className="p-6">Cargando…</main>;

  return (
    <main className="p-6 space-y-4">
      <h1 className="text-2xl font-bold">Área protegida</h1>
      <p>Bienvenido/Welcome: {userEmail}</p>
      <Link className="underline" href="/">Ir al MVP</Link>
      <div>
        <button
          onClick={async () => { await supabase.auth.signOut(); window.location.href="/login"; }}
          className="px-3 py-2 bg-gray-200 rounded"
        >
          Cerrar sesión
        </button>
      </div>
    </main>
  );
}
