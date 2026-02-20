import { supabase } from "@/lib/supabase";

type Member = {
  id: number;
  username: string | null;
  full_name: string | null;
  external_name: string | null;
  extra_role: string | null;
};

export default async function ChatPage({
  params,
}: {
  params: { token: string };
}) {
  const { data, error } = await supabase.rpc("members_for_token", {
    p_token: params.token,
  });

  const members = data as Member[] | null;

  if (error) {
    return (
      <main style={{ padding: 40 }}>
        <h1>Ошибка</h1>
        <pre>{JSON.stringify(error, null, 2)}</pre>
      </main>
    );
  }

  return (
    <main style={{ padding: 40 }}>
      <h1>Список участников</h1>

      {members?.length === 0 && <p>Список пуст</p>}

      <ul>
        {members?.map((m: Member, index: number) => (
          <li key={m.id}>
            {index + 1}. {m.external_name || m.full_name || "Без имени"}{" "}
            {m.username && `(@${m.username})`}{" "}
            {m.extra_role && `— ${m.extra_role}`}
          </li>
        ))}
      </ul>
    </main>
  );
}
