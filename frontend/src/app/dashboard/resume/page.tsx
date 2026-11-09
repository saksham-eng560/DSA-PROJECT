"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { Download, FileText, RefreshCw, Save, Sparkles, Upload } from "lucide-react";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { ResumeEditor } from "@/components/resume-editor";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Modal } from "@/components/modal";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/toast";
import { useIntegrations } from "@/hooks/use-applications";
import { ApiError, fetcher, post, put, upload } from "@/lib/api-client";
import type { Resume, ResumeContent } from "@/lib/types";
import { formatDate } from "@/lib/utils";

export default function ResumePage() {
  const { data: master, error, mutate, isLoading } = useSWR<Resume>("/resumes/master", fetcher, { shouldRetryOnError: false });
  const { data: tailored } = useSWR<{ items: Resume[] }>("/resumes?tailored=true", fetcher);
  const { data: integrations, mutate: refreshIntegrations } = useIntegrations();
  const [content, setContent] = useState<ResumeContent | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [template, setTemplate] = useState("classic");
  const fileRef = useRef<HTMLInputElement>(null);
  const toast = useToast();

  useEffect(() => {
    if (master?.parsed_content && !dirty) setContent(master.parsed_content);
  }, [master, dirty]);

  const onFile = async (file: File) => {
    setUploading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("is_master", "true");
      const created = await upload<Resume>("/resumes/upload", form);
      toast({ title: "Resume imported", description: created.parse_method === "llm" ? "Parsed with Claude — please review the result." : "Parsed with the built-in parser — please review the result.", tone: "success" });
      setDirty(false);
      mutate();
    } catch (err) {
      toast({ title: "Upload failed", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const importText = async () => {
    setUploading(true);
    try {
      await post("/resumes/from-text", { text: pasteText, is_master: true });
      toast({ title: "Resume imported", tone: "success" });
      setPasteOpen(false);
      setDirty(false);
      mutate();
    } catch (err) {
      toast({ title: "Import failed", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
    } finally {
      setUploading(false);
    }
  };

  const save = async () => {
    if (!master || !content) return;
    setSaving(true);
    try {
      await put(`/resumes/${master.id}`, { parsed_content: content });
      toast({ title: "Master resume saved", tone: "success" });
      setDirty(false);
      mutate();
    } catch (err) {
      toast({ title: "Save failed", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
    } finally {
      setSaving(false);
    }
  };

  const syncLinkedIn = async () => {
    try {
      await post("/users/me/integrations/linkedin/sync");
      toast({ title: "Syncing LinkedIn profile…", tone: "success" });
      setTimeout(() => refreshIntegrations(), 8000);
    } catch (err) {
      toast({ title: "Sync failed", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
    }
  };

  const hidden = (
    <input ref={fileRef} type="file" accept=".pdf,.docx,.txt,.md" className="hidden" onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])} />
  );
  const diff = integrations?.linkedin.profile_diff;

  if (isLoading) return <Skeleton className="h-96" />;

  if (error || !master) {
    return (
      <div>
        <PageHeader title="Resume Lab" description="Your master resume is the single source of truth — every tailored version is derived from it." />
        {hidden}
        <EmptyState icon={FileText} title="Upload your master resume"
          description="PDF, DOCX or plain text. It's parsed into structured sections you can review and edit. The agent never adds skills or experience that aren't in it."
          action={
            <div className="flex gap-2">
              <Button onClick={() => fileRef.current?.click()} loading={uploading}><Upload /> Upload file</Button>
              <Button variant="outline" onClick={() => setPasteOpen(true)}>Paste text</Button>
            </div>
          } />
        <Modal open={pasteOpen} onOpenChange={setPasteOpen} title="Paste your resume" className="max-w-2xl"
          footer={<Button onClick={importText} loading={uploading} disabled={pasteText.length < 50}>Import</Button>}>
          <Textarea value={pasteText} onChange={(e) => setPasteText(e.target.value)} className="min-h-[360px]" placeholder="Paste the full text of your resume…" />
        </Modal>
      </div>
    );
  }

  return (
    <div>
      {hidden}
      <PageHeader title="Resume Lab"
        description={`Master resume v${master.version} · updated ${formatDate(master.updated_at)}${master.original_filename ? ` · from ${master.original_filename}` : ""}`}
        actions={
          <>
            <Button variant="outline" onClick={() => fileRef.current?.click()} loading={uploading}><Upload /> Replace</Button>
            <Select className="w-32" value={template} onChange={(e) => setTemplate(e.target.value)} aria-label="PDF template">
              <option value="classic">Classic</option>
              <option value="modern">Modern</option>
            </Select>
            <a href={`/api/v1/resumes/${master.id}/pdf?template=${template}`} target="_blank" rel="noreferrer" className={buttonVariants({ variant: "outline" })}><Download /> PDF</a>
            <Button onClick={save} loading={saving} disabled={!dirty}><Save /> Save changes</Button>
          </>
        } />

      {diff?.has_changes && (
        <Card className="mb-6 border-primary/30 bg-primary/5">
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><Sparkles className="h-4 w-4 text-primary" /> LinkedIn has updates</CardTitle>
            <CardDescription>{diff.summary} — update the matching sections below if they&apos;re accurate.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-1 text-sm">
            {diff.changes.map((c, i) => <p key={i}><Badge tone="info" className="mr-2">{c.section}</Badge>{c.change}</p>)}
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <div className="xl:col-span-2">{content && <ResumeEditor value={content} onChange={(v) => { setContent(v); setDirty(true); }} />}</div>
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Preview</CardTitle>
              <CardDescription>Saved version, ATS-friendly single column</CardDescription>
            </CardHeader>
            <CardContent>
              <iframe key={`${master.version}-${template}`} src={`/api/v1/resumes/${master.id}/pdf?template=${template}`} title="Resume preview" className="h-[520px] w-full border" />
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="flex-row items-center justify-between space-y-0">
              <div>
                <CardTitle>LinkedIn sync</CardTitle>
                <CardDescription>{integrations?.linkedin.synced_at ? `Last synced ${formatDate(integrations.linkedin.synced_at)}` : "Detect profile changes to keep this resume current"}</CardDescription>
              </div>
              <Button size="sm" variant="outline" onClick={syncLinkedIn} disabled={!integrations?.linkedin.connected}><RefreshCw /> Sync</Button>
            </CardHeader>
            {!integrations?.linkedin.connected && (
              <CardContent className="text-sm text-muted-foreground">
                Connect LinkedIn with the Chrome extension in <Link href="/dashboard/settings?tab=integrations" className="text-primary hover:underline">Settings</Link>.
              </CardContent>
            )}
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Tailored versions</CardTitle>
              <CardDescription>{tailored?.items.length || 0} resumes generated for specific jobs</CardDescription>
            </CardHeader>
            <CardContent className="max-h-80 space-y-2 overflow-y-auto">
              {tailored?.items.map((r) => (
                <a key={r.id} href={r.pdf_url || `/api/v1/resumes/${r.id}/pdf`} target="_blank" rel="noreferrer"
                  className="flex items-center justify-between px-2 py-1.5 text-sm hover:bg-accent">
                  <span className="truncate">{r.label}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">{formatDate(r.created_at)}</span>
                </a>
              ))}
              {!tailored?.items.length && <p className="text-sm text-muted-foreground">None yet.</p>}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
