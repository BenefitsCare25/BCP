import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FileText, Plus, Upload } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { useMe } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { AlertDialog } from "@/components/ui/alert-dialog";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
  SheetBody,
} from "@/components/ui/sheet";
import { formatError } from "@/lib/errors";

export interface PolicyVersion {
  id: string;
  policy_id: string;
  version: number;
  title: string;
  category: string;
  status: "draft" | "published" | "archived";
  review_due: string | null;
  file_name: string;
  size_bytes: number;
  uploaded_by: string;
  created_at: string;
  published_by: string | null;
  published_at: string | null;
  archived_by: string | null;
  archived_at: string | null;
}
interface PolicyPage {
  items: PolicyVersion[];
  has_more: boolean;
}
const fieldClass =
  "w-full min-h-11 rounded-md border border-input bg-card px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
function dateLabel(value: string | null) {
  return value
    ? new Date(
        value.length === 10 ? `${value}T12:00:00` : value,
      ).toLocaleDateString("en-SG", {
        day: "numeric",
        month: "short",
        year: "numeric",
      })
    : "Not scheduled";
}

export function PolicyLibrary() {
  const { data: me } = useMe();
  const canManage = me?.role === "system_admin";
  const queryClient = useQueryClient();
  const [offset, setOffset] = useState(0);
  const [history, setHistory] = useState(false);
  const [upload, setUpload] = useState<PolicyVersion | "new" | null>(null);
  const [confirm, setConfirm] = useState<{
    record: PolicyVersion;
    action: "publish" | "archive";
  } | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ["ai-policies", me?.user_id, me?.role, offset, history],
    queryFn: () =>
      api.get<PolicyPage>(
        `/ai-policies?offset=${offset}&include_archived=${history}`,
      ),
    enabled: !!me,
  });
  const transition = useMutation({
    mutationFn: ({ record, action }: NonNullable<typeof confirm>) =>
      api.post<PolicyVersion>(`/ai-policies/${record.id}/${action}`, {}),
    onSuccess: async (_, input) => {
      await queryClient.invalidateQueries({ queryKey: ["ai-policies"] });
      setConfirm(null);
      toast.success(
        input.action === "publish"
          ? "Policy published to all broker users."
          : "Policy version archived. Its file is retained.",
      );
    },
    onError: (error) => toast.error(formatError(error)),
  });
  async function download(record: PolicyVersion) {
    setDownloading(record.id);
    try {
      const blob = await api.download(`/ai-policies/${record.id}/download`);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = record.file_name;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      toast.error(formatError(error));
    } finally {
      setDownloading(null);
    }
  }
  return (
    <section aria-label="AI policy library" className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-2xl">
          <h2 className="text-lg font-semibold">Policies &amp; documents</h2>
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
            Platform-wide policies for every company.{" "}
            {canManage
              ? "Upload a PDF, check the draft, then publish it for broker users."
              : "Read and download policies published by a system administrator."}
          </p>
        </div>
        {canManage && (
          <Button
            type="button"
            className="min-h-11"
            onClick={() => setUpload("new")}
          >
            <Plus className="size-4" aria-hidden="true" />
            Upload policy
          </Button>
        )}
      </div>
      <label className="inline-flex min-h-11 items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={history}
          onChange={(e) => {
            setHistory(e.target.checked);
            setOffset(0);
          }}
          className="size-4 accent-primary"
        />
        Include archived versions
      </label>
      {query.isPending ? (
        <p role="status" className="py-10 text-sm text-muted-foreground">
          Loading policies…
        </p>
      ) : query.isError ? (
        <div
          role="alert"
          className="space-y-3 rounded-lg border border-border bg-card p-5"
        >
          <p>Could not load policy documents. {formatError(query.error)}</p>
          <Button
            type="button"
            variant="outline"
            onClick={() => void query.refetch()}
          >
            Try again
          </Button>
        </div>
      ) : query.data.items.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border px-6 py-12 text-center">
          <FileText
            className="mx-auto mb-4 size-7 text-muted-foreground"
            aria-hidden="true"
          />
          <h3 className="font-semibold">
            {canManage
              ? "No policy documents yet"
              : "No published policies yet"}
          </h3>
          <p className="mx-auto mt-2 max-w-lg text-sm leading-relaxed text-muted-foreground">
            {canManage
              ? "Add your AI usage policy, data-handling rules or operating procedures. Each uploaded version is saved and can be downloaded here."
              : "A system administrator needs to upload and publish the platform policies. Published files will appear here."}
          </p>
        </div>
      ) : (
        <ul
          aria-label="Policy versions"
          className="divide-y divide-border rounded-lg border border-border bg-card"
        >
          {query.data.items.map((record) => (
            <li
              key={record.id}
              className="grid gap-5 p-5 lg:grid-cols-[minmax(0,1fr)_170px_200px]"
            >
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="break-words font-semibold">{record.title}</h3>
                  <span className="text-xs text-muted-foreground">
                    Version {record.version}
                  </span>
                  <Badge
                    variant={
                      record.status === "published"
                        ? "good"
                        : record.status === "draft"
                          ? "info"
                          : "default"
                    }
                  >
                    {record.status === "published"
                      ? "Published"
                      : record.status === "draft"
                        ? "Draft"
                        : "Archived"}
                  </Badge>
                </div>
                <p className="mt-2 break-all text-sm text-muted-foreground">
                  {record.category} · {record.file_name} ·{" "}
                  {Math.max(1, Math.round(record.size_bytes / 1024))} KB
                </p>
                <details className="mt-3 text-xs text-muted-foreground">
                  <summary className="w-fit cursor-pointer py-2 underline underline-offset-4">
                    Version details
                  </summary>
                  <dl className="mt-2 space-y-2">
                    <div>
                      <dt className="inline font-medium">Uploaded: </dt>
                      <dd className="inline">
                        {dateLabel(record.created_at)} by {record.uploaded_by}
                      </dd>
                    </div>
                    {record.published_at && (
                      <div>
                        <dt className="inline font-medium">Published: </dt>
                        <dd className="inline">
                          {dateLabel(record.published_at)} by{" "}
                          {record.published_by}
                        </dd>
                      </div>
                    )}
                    {record.archived_at && (
                      <div>
                        <dt className="inline font-medium">Archived: </dt>
                        <dd className="inline">
                          {dateLabel(record.archived_at)} by{" "}
                          {record.archived_by}
                        </dd>
                      </div>
                    )}
                  </dl>
                </details>
              </div>
              <div className="text-sm">
                <p className="text-xs text-muted-foreground">Review due</p>
                <p className="mt-2">{dateLabel(record.review_due)}</p>
                {record.review_due &&
                  record.status === "published" &&
                  new Date(`${record.review_due}T23:59:59`).getTime() <
                    Date.now() && (
                    <p className="mt-2 text-xs text-warn">Review overdue</p>
                  )}
              </div>
              <div className="flex flex-wrap items-start gap-2 lg:justify-end">
                <Button
                  type="button"
                  variant="outline"
                  className="min-h-11"
                  disabled={downloading === record.id}
                  aria-label={`Download ${record.title} version ${record.version}`}
                  onClick={() => void download(record)}
                >
                  <Download className="size-4" aria-hidden="true" />
                  {downloading === record.id ? "Downloading…" : "Download"}
                </Button>
                {canManage && (
                  <>
                    {record.status === "draft" && (
                      <Button
                        type="button"
                        className="min-h-11"
                        onClick={() =>
                          setConfirm({ record, action: "publish" })
                        }
                      >
                        Publish
                      </Button>
                    )}
                    <Button
                      type="button"
                      variant="ghost"
                      className="min-h-11"
                      onClick={() => setUpload(record)}
                    >
                      Add version
                    </Button>
                    {record.status !== "archived" && (
                      <Button
                        type="button"
                        variant="ghost"
                        className="min-h-11 text-muted-foreground"
                        onClick={() =>
                          setConfirm({ record, action: "archive" })
                        }
                      >
                        Archive
                      </Button>
                    )}
                  </>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      {(offset > 0 || query.data?.has_more) && (
        <div className="flex items-center justify-between gap-4">
          <Button
            type="button"
            variant="outline"
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 50))}
          >
            Previous
          </Button>
          <span className="text-sm text-muted-foreground">
            Page {offset / 50 + 1}
          </span>
          <Button
            type="button"
            variant="outline"
            disabled={!query.data?.has_more}
            onClick={() => setOffset(offset + 50)}
          >
            Next
          </Button>
        </div>
      )}
      <p className="text-xs leading-relaxed text-muted-foreground">
        Published versions remain available in the archive when replaced. The
        original PDF, publishing user and dates are retained.
      </p>
      <Sheet
        open={!!upload}
        onOpenChange={(open) => {
          if (!open) setUpload(null);
        }}
      >
        <SheetContent>
          <SheetHeader>
            <SheetTitle>
              {upload === "new" ? "Upload policy" : "Add policy version"}
            </SheetTitle>
            <SheetDescription>
              Saved as a draft. Only system administrators can see drafts.
            </SheetDescription>
          </SheetHeader>
          <SheetBody>
            {upload && (
              <PolicyUpload
                key={upload === "new" ? "new" : upload.id}
                previous={upload === "new" ? null : upload}
                onDone={() => {
                  setOffset(0);
                  setUpload(null);
                }}
              />
            )}
          </SheetBody>
        </SheetContent>
      </Sheet>
      <AlertDialog
        open={!!confirm}
        onOpenChange={(open) => {
          if (!open && !transition.isPending) setConfirm(null);
        }}
        title={
          confirm?.action === "publish"
            ? "Publish this policy version?"
            : "Archive this policy version?"
        }
        description={
          confirm?.action === "publish"
            ? `${confirm.record.title}, version ${confirm.record.version}, will become the current policy for all companies. Any previously published version will be archived.`
            : "This version will leave the current policy list. The file and its history will be retained."
        }
        confirmLabel={
          confirm?.action === "publish" ? "Publish policy" : "Archive version"
        }
        tone="info"
        confirmVariant="default"
        loading={transition.isPending}
        onConfirm={() => {
          if (confirm) transition.mutate(confirm);
        }}
      />
    </section>
  );
}

function PolicyUpload({
  previous,
  onDone,
}: {
  previous: PolicyVersion | null;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState(previous?.title ?? "");
  const [category, setCategory] = useState(
    previous?.category ?? "AI usage policy",
  );
  const [due, setDue] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState("");
  const save = useMutation({
    mutationFn: (form: FormData) =>
      api.upload<PolicyVersion>("/ai-policies", form),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["ai-policies"] });
      toast.success("Policy draft saved.");
      onDone();
    },
    onError: (err) => setError(formatError(err)),
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!file || !title.trim()) {
      setError("Enter a title and choose a PDF.");
      return;
    }
    if (
      !file.name.toLowerCase().endsWith(".pdf") ||
      file.size > 10 * 1024 * 1024
    ) {
      setError("Choose a PDF no larger than 10 MB.");
      return;
    }
    const form = new FormData();
    form.set("title", title.trim());
    form.set("category", category);
    form.set("file", file);
    if (due) form.set("review_due", due);
    if (previous) form.set("previous_id", previous.id);
    setError("");
    save.mutate(form);
  }
  return (
    <form onSubmit={submit} className="space-y-5">
      {error && (
        <p role="alert" className="text-sm text-error">
          {error}
        </p>
      )}
      <div className="space-y-2">
        <label htmlFor="policy-title" className="block text-sm font-medium">
          Policy title
        </label>
        <Input
          id="policy-title"
          required
          maxLength={160}
          value={title}
          disabled={!!previous || save.isPending}
          onChange={(e) => setTitle(e.target.value)}
        />
      </div>
      <div className="space-y-2">
        <label htmlFor="policy-category" className="block text-sm font-medium">
          Document type
        </label>
        <select
          id="policy-category"
          value={category}
          disabled={!!previous || save.isPending}
          onChange={(e) => setCategory(e.target.value)}
          className={fieldClass}
        >
          {[
            "AI usage policy",
            "Data handling",
            "Operating procedure",
            "Other",
          ].map((value) => (
            <option key={value}>{value}</option>
          ))}
        </select>
      </div>
      <div className="space-y-2">
        <label htmlFor="policy-due" className="block text-sm font-medium">
          Review due (optional)
        </label>
        <Input
          id="policy-due"
          type="date"
          value={due}
          disabled={save.isPending}
          onChange={(e) => setDue(e.target.value)}
        />
      </div>
      <div className="space-y-2">
        <label htmlFor="policy-file" className="block text-sm font-medium">
          Policy PDF
        </label>
        <input
          id="policy-file"
          type="file"
          accept=".pdf,application/pdf"
          required
          disabled={save.isPending}
          aria-describedby="policy-file-hint"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className={`${fieldClass} text-xs file:mr-2 file:rounded file:border-0 file:bg-muted file:p-2 file:text-foreground`}
        />
        <p id="policy-file-hint" className="text-xs text-muted-foreground">
          PDF only, up to 10 MB. Each upload creates a separate saved version.
        </p>
      </div>
      <div className="flex justify-end gap-2">
        <Button
          type="button"
          variant="outline"
          disabled={save.isPending}
          onClick={onDone}
        >
          Cancel
        </Button>
        <Button type="submit" disabled={save.isPending}>
          <Upload className="size-4" aria-hidden="true" />
          {save.isPending ? "Saving…" : "Save draft"}
        </Button>
      </div>
    </form>
  );
}
