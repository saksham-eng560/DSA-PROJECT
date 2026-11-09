"use client";

import { useState } from "react";
import { AlertTriangle, CheckCircle2, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/modal";
import type { ApplicationDetail, CustomAnswer } from "@/lib/types";

export function ApprovalModal({ app, open, onOpenChange, onApprove, answers, coverLetter }: {
  app: ApplicationDetail;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onApprove: () => Promise<void>;
  answers: CustomAnswer[];
  coverLetter: string;
}) {
  const [confirmed, setConfirmed] = useState(false);
  const [loading, setLoading] = useState(false);
  const flagged = answers.filter((a) => a.needs_user_review);
  const emptyRequired = answers.filter((a) => a.required && !a.answer?.trim());
  const unmapped = app.form_fields.filter((f) => f.status === "unmapped" && f.required);

  const approve = async () => {
    setLoading(true);
    try {
      await onApprove();
      onOpenChange(false);
    } finally {
      setLoading(false);
    }
  };

  const checks = [
    { ok: !!app.tailored_resume_pdf_url, text: "Tailored resume PDF attached" },
    { ok: !!coverLetter.trim() || !app.job, text: coverLetter.trim() ? `Cover letter (${coverLetter.trim().split(/\s+/).length} words)` : "No cover letter" },
    { ok: flagged.length === 0, text: flagged.length ? `${flagged.length} answer(s) were flagged for review — make sure you checked them` : "All answers reviewed" },
    { ok: emptyRequired.length === 0, text: emptyRequired.length ? `${emptyRequired.length} required answer(s) are empty — fill them in before approving` : "Required answers filled" },
    { ok: unmapped.length === 0, text: unmapped.length ? `${unmapped.length} required form field(s) could not be filled automatically` : "Form fields filled" },
  ];

  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Approve & submit application"
      description={`${app.job?.role_title} at ${app.job?.company_name}`}
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={approve} disabled={!confirmed || emptyRequired.length > 0} loading={loading}><ShieldCheck /> Approve & submit</Button>
        </>
      }>
      <ul className="space-y-2">
        {checks.map((c) => (
          <li key={c.text} className="flex items-start gap-2 text-sm">
            {c.ok ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" /> : <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />}
            {c.text}
          </li>
        ))}
      </ul>
      {emptyRequired.length > 0 && (
        <ul className="mt-2 list-disc space-y-1 pl-10 text-sm text-muted-foreground">
          {emptyRequired.map((a) => <li key={a.field_id || a.question}>{a.question}</li>)}
        </ul>
      )}
      {app.needs_manual_review && app.manual_review_reason && (
        <p className="mt-4 bg-warning/10 p-3 text-sm text-warning">{app.manual_review_reason}</p>
      )}
      <p className="mt-4 text-sm text-muted-foreground">
        The agent will re-open the application form, fill it with exactly what you reviewed (including your edits), and click submit.
        A confirmation screenshot is saved.
      </p>
      <label className="mt-4 flex items-start gap-2 text-sm">
        <input type="checkbox" className="mt-0.5 h-4 w-4 accent-[hsl(var(--primary))]" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
        I reviewed the resume, cover letter and answers, and everything is accurate.
      </label>
    </Modal>
  );
}
