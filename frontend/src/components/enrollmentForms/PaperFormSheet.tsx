/** File a scanned paper enrolment form for a member without portal access.
 *
 * The member is picked from the period's own roster, so a form can only be
 * filed against someone the period covers. The scan goes through the same
 * malware-scanned upload path as claim documents (PDF, PNG or JPG, 15 MB). */
import { Loader2, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { useEnrollmentRoster } from "@/api/enrollment";
import { useFilePaperForm } from "@/api/enrollmentForms";
import { EmployeePicker } from "@/components/operations/EmployeePicker";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { formatError } from "@/lib/errors";
import { useDebouncedValue } from "@/lib/use-debounced-value";

export function PaperFormSheet({
  open,
  onOpenChange,
  policyYearId,
  windowId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  policyYearId: string;
  windowId: string | undefined;
}) {
  const [query, setQuery] = useState("");
  const [employeeId, setEmployeeId] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const q = useDebouncedValue(query, 300);
  const roster = useEnrollmentRoster(open ? windowId : undefined, { q: q || undefined, limit: 30 });
  const file_ = useFilePaperForm(policyYearId);

  const reset = () => {
    setQuery("");
    setEmployeeId(null);
    setNote("");
    setFile(null);
  };

  async function submit() {
    if (!employeeId || !file) return;
    try {
      const created = await file_.mutateAsync({ employeeId, windowId, note, file });
      toast.success(`Filed as ${created.reference_no}`);
      reset();
      onOpenChange(false);
    } catch (e) {
      toast.error(formatError(e));
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>File a paper form</SheetTitle>
          <SheetDescription>
            For staff who completed a paper enrolment form. It joins the register beside online
            forms and replaces any earlier form from the same member for this period.
          </SheetDescription>
        </SheetHeader>
        <SheetBody className="space-y-4">
          {!windowId ? (
            <p className="text-sm text-muted-foreground">Choose an enrolment period first.</p>
          ) : (
            <EmployeePicker
              items={(roster.data?.items ?? []).map((m) => ({
                id: m.employee_id,
                name: m.employee_name ?? m.staff_id,
                subtitle: m.staff_id,
              }))}
              selectedId={employeeId}
              onSelect={setEmployeeId}
              isLoading={roster.isLoading}
              query={query}
              onQueryChange={setQuery}
              listMaxHeight="max-h-64"
            />
          )}
          <div className="space-y-1.5">
            <Label htmlFor="paper-form-note">Note (optional)</Label>
            <Input
              id="paper-form-note"
              value={note}
              maxLength={2000}
              onChange={(e) => setNote(e.target.value)}
              placeholder="e.g. Received by email from HR on 3 Oct"
            />
          </div>
          <div className="space-y-1.5">
            <input
              ref={fileInput}
              type="file"
              accept=".pdf,.png,.jpg,.jpeg"
              className="hidden"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
            <Button variant="outline" onClick={() => fileInput.current?.click()}>
              <Upload className="size-4" />
              {file ? file.name : "Choose the scanned form"}
            </Button>
            <p className="text-xs text-muted-foreground">PDF, PNG or JPG, up to 15 MB.</p>
          </div>
        </SheetBody>
        <SheetFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={!employeeId || !file || file_.isPending} onClick={() => void submit()}>
            {file_.isPending && <Loader2 className="size-4 animate-spin" />}
            File form
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
