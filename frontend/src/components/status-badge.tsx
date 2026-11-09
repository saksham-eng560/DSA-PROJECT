import { Badge } from "@/components/ui/badge";
import type { ApplicationStatus } from "@/lib/types";
import { STATUS_LABELS, STATUS_TONE } from "@/lib/utils";

export function StatusBadge({ status }: { status: ApplicationStatus }) {
  return <Badge tone={STATUS_TONE[status] || "default"}>{STATUS_LABELS[status] || status}</Badge>;
}
