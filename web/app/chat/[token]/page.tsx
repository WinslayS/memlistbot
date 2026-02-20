type Member = {
  id: number;
  username: string | null;
  full_name: string | null;
  external_name: string | null;
  extra_role: string | null;
};

type PageProps = {
  params: Promise<{ token: string }>;
};

export default async function ChatPage({ params }: PageProps) {
  const { token } = await params;

  const res = await fetch(`http://localhost:3000/api/chat/${token}`, {
    cache: "no-store",
  });

  const json = await res.json();

  if (!res.ok) {
    return <pre>{JSON.stringify(json, null, 2)}</pre>;
  }

  const members: Member[] = json.members ?? [];

  return (
    <main style={{ padding: 40 }}>
      <h1>Список участников</h1>

      {members.length === 0 && <p>Список пуст</p>}

      <ul>
        {members.map((m, index) => (
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
