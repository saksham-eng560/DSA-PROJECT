import type { LucideIcon } from "lucide-react";

export function EmptyState({ icon: Icon, title, description, action }: {
  icon: LucideIcon;
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center border border-dashed border-line/70 p-10 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-full border border-foreground/40">
        <Icon className="h-5 w-5 text-foreground/80" />
      </div>
      <h3 className="label-caps mt-4 text-[13px] font-bold">{title}</h3>
      {description && <p className="mt-2 max-w-sm text-sm text-muted-foreground">{description}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}
