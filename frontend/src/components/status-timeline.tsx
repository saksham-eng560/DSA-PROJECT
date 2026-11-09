import type { HistoryEntry } from "@/lib/types";
import { STATUS_LABELS, formatDateTime, titleCase } from "@/lib/utils";

export function StatusTimeline({ history }: { history: HistoryEntry[] }) {
  if (!history.length) return <p className="text-sm text-muted-foreground">No status changes yet.</p>;
  return (
    <ol className="relative space-y-4 border-l border-line/70 pl-5">
      {history.map((h) => (
        <li key={h.id} className="relative">
          <span className="absolute -left-[1.55rem] top-1 h-2.5 w-2.5 bg-primary" />
          <p className="text-sm font-medium">{STATUS_LABELS[h.new_status] || h.new_status}</p>
          <p className="text-xs text-muted-foreground">
            {formatDateTime(h.created_at)} · by {titleCase(h.changed_by)}
          </p>
          {h.notes && <p className="mt-0.5 text-xs text-muted-foreground">{h.notes}</p>}
        </li>
      ))}
    </ol>
  );
}
