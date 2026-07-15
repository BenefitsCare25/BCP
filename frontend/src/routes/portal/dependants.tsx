/** "My dependants" — list + self-service addition. Added dependants are
 * pending until the broker approves them (optionally with a proof document). */
import { useRef, useState } from "react";
import { Loader2, Paperclip, UserPlus, Users } from "lucide-react";
import { toast } from "sonner";
import {
  useAddDependant,
  usePortalDependants,
  useUploadDependantProof,
} from "@/api/portal";
import { formatError, isNotFoundError } from "@/lib/errors";
import { DependantsTable } from "@/components/portal/DependantsTable";
import { PortalErrorState } from "@/components/portal/PortalErrorState";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

export function PortalDependantsPage() {
  const dependants = usePortalDependants();
  const addDependant = useAddDependant();
  const uploadProof = useUploadDependantProof();

  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [relationship, setRelationship] = useState("");
  const [dob, setDob] = useState("");
  const [proof, setProof] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const submit = async () => {
    setError(null);
    if (!name.trim() || !relationship.trim()) {
      setError("Name and relationship are required.");
      return;
    }
    setBusy(true);
    try {
      const created = await addDependant.mutateAsync({
        name: name.trim(),
        relationship: relationship.trim(),
        dob: dob || null,
      });
      if (proof) {
        await uploadProof.mutateAsync({ dependantId: created.id, file: proof });
      }
      toast.success("Dependant submitted for approval");
      setShowForm(false);
      setName("");
      setRelationship("");
      setDob("");
      setProof(null);
    } catch (err) {
      setError(formatError(err));
    } finally {
      setBusy(false);
    }
  };

  if (dependants.isLoading) return <Skeleton className="h-48 w-full" />;

  // A fetch failure must not read as "no dependants on record" — only a 404
  // falls through to the empty state.
  if (dependants.isError && !isNotFoundError(dependants.error)) {
    return <PortalErrorState onRetry={() => void dependants.refetch()} />;
  }

  const rows = dependants.data ?? [];
  const selectClass =
    "w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus-ring";

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-foreground">My dependants</h2>
        {!showForm && (
          <Button size="sm" onClick={() => setShowForm(true)}>
            <UserPlus className="size-4" />
            <span className="ml-1">Add dependant</span>
          </Button>
        )}
      </div>

      {showForm && (
        <div className="rounded-lg border border-border bg-card p-5 space-y-3">
          <div>
            <h3 className="text-sm font-semibold text-foreground">
              Add a dependant
            </h3>
            <p className="mt-1 text-xs text-muted-foreground">
              Your broker reviews every addition before coverage applies. Attach
              a supporting document (birth or marriage certificate) to speed it
              up.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="col-span-2 space-y-1.5">
              <Label htmlFor="dep-name">Full name</Label>
              <Input
                id="dep-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoFocus
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="dep-rel">Relationship</Label>
              <select
                id="dep-rel"
                className={selectClass}
                value={relationship}
                onChange={(e) => setRelationship(e.target.value)}
              >
                <option value="">Select…</option>
                <option value="spouse">Spouse</option>
                <option value="child">Child</option>
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="dep-dob">Date of birth</Label>
              <Input
                id="dep-dob"
                type="date"
                value={dob}
                onChange={(e) => setDob(e.target.value)}
              />
            </div>
          </div>
          <div>
            <input
              ref={fileInput}
              type="file"
              accept=".pdf,.png,.jpg,.jpeg"
              className="hidden"
              onChange={(e) => setProof(e.target.files?.[0] ?? null)}
            />
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => fileInput.current?.click()}
            >
              <Paperclip className="size-4" />
              {proof ? proof.name : "Attach proof (optional)"}
            </Button>
          </div>
          {error && <p className="text-xs text-error">{error}</p>}
          <div className="flex gap-2">
            <Button disabled={busy} onClick={() => void submit()}>
              {busy && <Loader2 className="size-4 animate-spin" />}
              Submit for approval
            </Button>
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() => {
                setShowForm(false);
                setError(null);
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}

      {rows.length === 0 && !showForm ? (
        <div className="rounded-lg border border-border bg-card p-8 text-center">
          <Users className="mx-auto size-6 text-muted-foreground" />
          <p className="mt-2 text-sm font-medium text-foreground">
            No dependants on record
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            Add a dependant above — your broker approves it before coverage
            applies.
          </p>
        </div>
      ) : rows.length > 0 ? (
        <DependantsTable rows={rows} />
      ) : null}
    </div>
  );
}
