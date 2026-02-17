import { supabase } from "@/lib/supabase";

export default async function Home() {
  const { data, error } = await supabase.from("members").select("*").limit(1);

  if (error) {
    return (
      <main style={{ padding: 40 }}>
        <h1>Ошибка подключения</h1>
        <pre>{JSON.stringify(error, null, 2)}</pre>
      </main>
    );
  }

  return (
    <main style={{ padding: 40 }}>
      <h1>Подключение к Supabase работает ✅</h1>
      <pre>{JSON.stringify(data, null, 2)}</pre>
    </main>
  );
}
