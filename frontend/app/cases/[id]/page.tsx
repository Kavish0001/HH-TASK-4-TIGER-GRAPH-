import { CaseView } from "@/components/case/case-view";

export default async function CasePage({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<Record<string, string | undefined>> }) {
  const { id } = await params;
  const sp = await searchParams;
  return <CaseView id={decodeURIComponent(id)} autoRun={sp.run === "1"} />;
}
