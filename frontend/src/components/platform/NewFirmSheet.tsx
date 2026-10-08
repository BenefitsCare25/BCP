import { useId, useState, type FormEvent } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { toast } from "sonner";
import { type PlatformFirm, useCreatePlatformFirm } from "@/api/platform";
import { InviteUserForm } from "@/components/admin/InviteUserForm";
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
import { aliasProblem } from "./domainMeta";

/** Create a broker firm, then invite its first firm admin through the same
 *  invitation flow as the Users card (role fixed to firm_admin, firm fixed to
 *  the new one). The invite can be skipped and sent later from the firm's
 *  page. */
export function NewFirmSheet({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  // Mounted afresh for each opening (the caller keys it), so this step state
  // never outlives one creation.
  const [created, setCreated] = useState<PlatformFirm | null>(null);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent>
        {created ? (
          <FirstAdminStep firm={created} onDone={() => onOpenChange(false)} />
        ) : (
          <CreateFirmStep onCreated={setCreated} onCancel={() => onOpenChange(false)} />
        )}
      </SheetContent>
    </Sheet>
  );
}

function CreateFirmStep({
  onCreated,
  onCancel,
}: {
  onCreated: (firm: PlatformFirm) => void;
  onCancel: () => void;
}) {
  const create = useCreatePlatformFirm();
  const id = useId();
  const [name, setName] = useState("");
  const [alias, setAlias] = useState("");
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const trimmed = name.trim();
    const slug = alias.trim().toLowerCase();
    if (!trimmed) {
      setError("Enter the broker firm's name.");
      return;
    }
    const problem = aliasProblem(slug);
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    try {
      const firm = await create.mutateAsync({ name: trimmed, ...(slug ? { slug } : {}) });
      toast.success(`${firm.name} created`);
      onCreated(firm);
    } catch (e) {
      // 409 alias or name already taken, 422 an alias the server will not use.
      setError(formatError(e));
    }
  };

  const errorId = `${id}-error`;
  return (
    <form onSubmit={(event) => void submit(event)} className="flex h-full flex-col">
      <SheetHeader>
        <SheetTitle>New broker firm</SheetTitle>
        <SheetDescription>
          A firm is a separate broker on the platform with its own companies,
          users and web addresses.
        </SheetDescription>
      </SheetHeader>
      <SheetBody className="space-y-4">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-name`}>Firm name</Label>
          <Input
            id={`${id}-name`}
            value={name}
            autoFocus
            maxLength={200}
            aria-invalid={error && !name.trim() ? true : undefined}
            aria-describedby={error ? errorId : undefined}
            onChange={(e) => {
              setName(e.target.value);
              setError(null);
            }}
            placeholder="Harbour Insurance Brokers"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-alias`}>Alias (optional)</Label>
          <Input
            id={`${id}-alias`}
            value={alias}
            spellCheck={false}
            autoComplete="off"
            maxLength={63}
            aria-describedby={`${id}-alias-help${error ? ` ${errorId}` : ""}`}
            onChange={(e) => {
              setAlias(e.target.value);
              setError(null);
            }}
            placeholder="harbour"
          />
          <p id={`${id}-alias-help`} className="text-xs text-muted-foreground">
            A short name used in the firm&apos;s web addresses: lowercase letters,
            numbers and hyphens. Left blank, it is derived from the firm name.
          </p>
        </div>
        {error && (
          <p id={errorId} role="alert" className="text-sm text-error">
            {error}
          </p>
        )}
      </SheetBody>
      <SheetFooter>
        <Button type="button" variant="outline" onClick={onCancel} disabled={create.isPending}>
          Cancel
        </Button>
        <Button type="submit" loading={create.isPending}>
          Create firm
        </Button>
      </SheetFooter>
    </form>
  );
}

function FirstAdminStep({ firm, onDone }: { firm: PlatformFirm; onDone: () => void }) {
  const navigate = useNavigate();
  return (
    <div className="flex h-full flex-col">
      <SheetHeader>
        <SheetTitle>Invite {firm.name}&apos;s first firm admin</SheetTitle>
        <SheetDescription>
          A firm admin runs the firm: its users, web addresses and removal of
          saved data. You can also invite one later from the firm&apos;s page.
        </SheetDescription>
      </SheetHeader>
      <SheetBody className="space-y-4">
        <p className="text-sm text-muted-foreground">
          {firm.name} was created with the alias{" "}
          <span className="font-medium text-foreground">{firm.slug}</span>. Its
          web addresses are added on the firm&apos;s page.
        </p>
        <InviteUserForm
          idPrefix="first-firm-admin"
          roleOptions={["firm_admin"]}
          defaultRole="firm_admin"
          firmId={firm.id}
          onInvited={() => {
            onDone();
            void navigate({ to: "/platform/firms/$firmId", params: { firmId: firm.id } });
          }}
        />
      </SheetBody>
      <SheetFooter>
        <Button asChild variant="outline">
          <Link to="/platform/firms/$firmId" params={{ firmId: firm.id }} onClick={onDone}>
            Open {firm.name}
          </Link>
        </Button>
        <Button type="button" variant="ghost" onClick={onDone}>
          Skip for now
        </Button>
      </SheetFooter>
    </div>
  );
}
