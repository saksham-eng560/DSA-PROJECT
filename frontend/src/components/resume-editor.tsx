"use client";

import { useId } from "react";
import { Plus, Trash2 } from "lucide-react";
import { TagInput } from "@/components/tag-input";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { ResumeContent } from "@/lib/types";

type Setter = (next: ResumeContent) => void;

function Section({ title, children, onAdd }: { title: string; children: React.ReactNode; onAdd?: () => void }) {
  return (
    <section className="border p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-bold uppercase leading-none tracking-[0.12em]">{title}</h3>
        {onAdd && <Button type="button" variant="ghost" size="sm" onClick={onAdd}><Plus /> Add</Button>}
      </div>
      <div className="space-y-4">{children}</div>
    </section>
  );
}

function Field({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (v: string) => void; placeholder?: string }) {
  const id = useId();
  return (
    <div className="space-y-1">
      <Label htmlFor={id} className="text-xs text-muted-foreground">{label}</Label>
      <Input id={id} value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} />
    </div>
  );
}

function linesToList(value: string) {
  return value.split("\n").map((s) => s.replace(/^[•\-*]\s*/, "").trim()).filter(Boolean);
}

export function ResumeEditor({ value, onChange }: { value: ResumeContent; onChange: Setter }) {
  const set = <K extends keyof ResumeContent>(key: K, v: ResumeContent[K]) => onChange({ ...value, [key]: v });
  const updateAt = <K extends "experience" | "education" | "projects" | "certifications">(key: K, index: number, patch: Partial<ResumeContent[K][number]>) =>
    set(key, value[key].map((item, i) => (i === index ? { ...item, ...patch } : item)) as ResumeContent[K]);
  const removeAt = <K extends "experience" | "education" | "projects" | "certifications">(key: K, index: number) =>
    set(key, value[key].filter((_, i) => i !== index) as ResumeContent[K]);
  const info = value.personal_info;

  return (
    <div className="space-y-4">
      <Section title="Contact">
        <div className="grid gap-3 sm:grid-cols-2">
          {(["name", "email", "phone", "location", "linkedin", "github", "portfolio"] as const).map((k) => (
            <Field key={k} label={k[0].toUpperCase() + k.slice(1)} value={info[k] || ""} onChange={(v) => set("personal_info", { ...info, [k]: v })} />
          ))}
        </div>
      </Section>

      <Section title="Summary">
        <Textarea aria-label="Summary" value={value.summary} onChange={(e) => set("summary", e.target.value)} className="min-h-[90px]" />
      </Section>

      <Section title="Experience" onAdd={() => set("experience", [...value.experience, { company: "", title: "", start_date: "", end_date: "", location: "", bullets: [] }])}>
        {value.experience.map((exp, i) => (
          <div key={i} className="bg-muted/40 p-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Title" value={exp.title} onChange={(v) => updateAt("experience", i, { title: v })} />
              <Field label="Company" value={exp.company} onChange={(v) => updateAt("experience", i, { company: v })} />
              <Field label="Start" value={exp.start_date} onChange={(v) => updateAt("experience", i, { start_date: v })} placeholder="Jan 2022" />
              <Field label="End" value={exp.end_date} onChange={(v) => updateAt("experience", i, { end_date: v })} placeholder="Present" />
              <Field label="Location" value={exp.location} onChange={(v) => updateAt("experience", i, { location: v })} />
            </div>
            <div className="mt-3 space-y-1">
              <Label className="text-xs text-muted-foreground">Achievements (one per line)</Label>
              <Textarea aria-label="Achievements (one per line)" value={exp.bullets.join("\n")} onChange={(e) => updateAt("experience", i, { bullets: linesToList(e.target.value) })} className="min-h-[110px]" />
            </div>
            <Button type="button" variant="ghost" size="sm" className="mt-2 text-destructive" onClick={() => removeAt("experience", i)}><Trash2 /> Remove</Button>
          </div>
        ))}
      </Section>

      <Section title="Education" onAdd={() => set("education", [...value.education, { institution: "", degree: "", field: "", gpa: "", start_date: "", end_date: "", highlights: [] }])}>
        {value.education.map((edu, i) => (
          <div key={i} className="bg-muted/40 p-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Institution" value={edu.institution} onChange={(v) => updateAt("education", i, { institution: v })} />
              <Field label="Degree" value={edu.degree} onChange={(v) => updateAt("education", i, { degree: v })} />
              <Field label="Field" value={edu.field} onChange={(v) => updateAt("education", i, { field: v })} />
              <Field label="GPA" value={edu.gpa} onChange={(v) => updateAt("education", i, { gpa: v })} />
              <Field label="Start" value={edu.start_date} onChange={(v) => updateAt("education", i, { start_date: v })} />
              <Field label="End" value={edu.end_date} onChange={(v) => updateAt("education", i, { end_date: v })} />
            </div>
            <Button type="button" variant="ghost" size="sm" className="mt-2 text-destructive" onClick={() => removeAt("education", i)}><Trash2 /> Remove</Button>
          </div>
        ))}
      </Section>

      <Section title="Projects" onAdd={() => set("projects", [...value.projects, { name: "", description: "", technologies: [], url: "" }])}>
        {value.projects.map((p, i) => (
          <div key={i} className="bg-muted/40 p-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Name" value={p.name} onChange={(v) => updateAt("projects", i, { name: v })} />
              <Field label="URL" value={p.url} onChange={(v) => updateAt("projects", i, { url: v })} />
            </div>
            <div className="mt-3 space-y-1">
              <Label className="text-xs text-muted-foreground">Description</Label>
              <Textarea aria-label="Description" value={p.description} onChange={(e) => updateAt("projects", i, { description: e.target.value })} />
            </div>
            <div className="mt-3 space-y-1">
              <Label className="text-xs text-muted-foreground">Technologies</Label>
              <TagInput aria-label="Technologies" value={p.technologies} onChange={(v) => updateAt("projects", i, { technologies: v })} placeholder="Type and press Enter" />
            </div>
            <Button type="button" variant="ghost" size="sm" className="mt-2 text-destructive" onClick={() => removeAt("projects", i)}><Trash2 /> Remove</Button>
          </div>
        ))}
      </Section>

      <Section title="Skills">
        {(["technical", "tools", "languages", "soft_skills"] as const).map((k) => (
          <div key={k} className="space-y-1">
            <Label className="text-xs text-muted-foreground">{k === "soft_skills" ? "Soft skills" : k[0].toUpperCase() + k.slice(1)}</Label>
            <TagInput aria-label={k === "soft_skills" ? "Soft skills" : k[0].toUpperCase() + k.slice(1)} value={value.skills[k]} onChange={(v) => set("skills", { ...value.skills, [k]: v })} placeholder="Type and press Enter" />
          </div>
        ))}
      </Section>

      <Section title="Certifications" onAdd={() => set("certifications", [...value.certifications, { name: "", issuer: "", date: "" }])}>
        {value.certifications.map((c, i) => (
          <div key={i} className="grid items-end gap-3 sm:grid-cols-[1fr_1fr_8rem_auto]">
            <Field label="Name" value={c.name} onChange={(v) => updateAt("certifications", i, { name: v })} />
            <Field label="Issuer" value={c.issuer} onChange={(v) => updateAt("certifications", i, { issuer: v })} />
            <Field label="Date" value={c.date} onChange={(v) => updateAt("certifications", i, { date: v })} />
            <Button type="button" variant="ghost" size="icon" onClick={() => removeAt("certifications", i)} aria-label="Remove certification"><Trash2 /></Button>
          </div>
        ))}
      </Section>

      <Section title="Awards">
        <TagInput aria-label="Awards" value={value.awards} onChange={(v) => set("awards", v)} placeholder="Type and press Enter" />
      </Section>
    </div>
  );
}
